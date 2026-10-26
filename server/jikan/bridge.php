<?php
declare(strict_types=1);

// CLI-only adapter. Python owns HTTP, caching and rate limits; Jikan owns parsing.
require __DIR__ . '/vendor/autoload.php';

use Jikan\MyAnimeList\MalClient;
use Jikan\Request\Anime;
use Jikan\Request\Search\AnimeSearchRequest;
use Jikan\Request\Top\TopAnimeRequest;
use Jikan\Request\Seasonal\SeasonalRequest;
use Jikan\Request\Genre\AnimeGenresRequest;
use Jikan\Model\Common\DateRange;
use Jikan\Model\Common\MalUrl;
use Symfony\Component\HttpClient\MockHttpClient;
use Symfony\Component\HttpClient\Response\MockResponse;

function endpoint(string $path, array $p): array
{
    $page = (int)($p['page'] ?? 1);
    if ($path === 'anime') {
        $r = new AnimeSearchRequest($p['q'] ?? '', $page);
        $types = ['tv' => 1, 'ova' => 2, 'movie' => 3, 'special' => 4, 'ona' => 5, 'music' => 6];
        $statuses = ['airing' => 1, 'complete' => 2, 'upcoming' => 3];
        $orders = ['title' => 0, 'start_date' => 2, 'score' => 3, 'episodes' => 4, 'popularity' => 7];
        if (!empty($p['type'])) $r->setType((string)$types[$p['type']]);
        if (!empty($p['status'])) $r->setStatus($statuses[$p['status']]);
        if (!empty($p['min_score'])) $r->setScore((float)$p['min_score']);
        if (!empty($p['genres'])) $r->setGenre(...array_map('intval', explode(',', $p['genres'])));
        foreach (['start_date' => 'setStartDate', 'end_date' => 'setEndDate'] as $key => $setter) {
            if (!empty($p[$key])) {
                [$y, $m, $d] = array_map('intval', explode('-', $p[$key]));
                $r->$setter($d, $m, $y);
            }
        }
        $order = $p['order_by'] ?? 'popularity';
        $r->setOrderBy($orders[$order]);
        $r->setSort(($p['sort'] ?? ($order === 'title' ? 'asc' : 'desc')) === 'asc' ? 0 : 1);
        return ['getAnimeSearch', $r, 50];
    }
    if ($path === 'top/anime') return ['getTopAnime', new TopAnimeRequest($page, $p['filter'] ?? 'bypopularity'), 50];
    if ($path === 'genres/anime') return ['getAnimeGenres', new AnimeGenresRequest(), 0];
    if (preg_match('~^seasons/(\d{4})/(winter|spring|summer|fall)$~D', $path, $m)) {
        return ['getSeasonal', new SeasonalRequest((int)$m[1], $m[2]), 0];
    }
    if (preg_match('~^anime/([1-9]\d*)(?:/(full|characters|recommendations|episodes))?$~D', $path, $m)) {
        $id = (int)$m[1];
        return match ($m[2] ?? 'full') {
            'full' => ['getAnime', new Anime\AnimeRequest($id), 0],
            'characters' => ['getAnimeCharactersAndStaff', new Anime\AnimeCharactersAndStaffRequest($id), 0],
            'recommendations' => ['getAnimeRecommendations', new Anime\AnimeRecommendationsRequest($id), 0],
            'episodes' => ['getAnimeEpisodes', new Anime\AnimeEpisodesRequest($id, $page), 100],
        };
    }
    throw new InvalidArgumentException('Unsupported catalog endpoint');
}

function normalize(mixed $value): mixed
{
    if ($value instanceof DateTimeInterface) return $value->format(DATE_ATOM);
    if ($value instanceof DateRange) return [
        'from' => normalize($value->getFrom()), 'to' => normalize($value->getUntil()),
        'prop' => ['from' => normalize($value->getFromProp()), 'to' => normalize($value->getUntilProp())],
        'string' => (string)$value,
    ];
    if ($value instanceof MalUrl) return [
        'mal_id' => $value->getMalId(), 'type' => $value->getType(),
        'name' => $value->getName(), 'url' => $value->getUrl(),
    ];
    if (is_array($value)) return array_map('normalize', $value);
    if (!is_object($value)) return $value;
    if (!str_starts_with(get_class($value), 'Jikan\\Model\\')) throw new LogicException('Unexpected model');
    $result = [];
    foreach ((new ReflectionObject($value))->getMethods(ReflectionMethod::IS_PUBLIC) as $method) {
        if ($method->isStatic() || $method->getNumberOfRequiredParameters() !== 0) continue;
        if (!preg_match('/^(?:get|is)([A-Z].*)$/D', $method->name, $m)) continue;
        $key = strtolower(preg_replace('/(?<!^)[A-Z]/', '_$0', $m[1]));
        $result[$key] = normalize($method->invoke($value));
    }
    if ($value instanceof Jikan\Model\Anime\Anime) {
        $result['relations'] = [];
        foreach ($value->getRelated() as $relation => $entries) {
            $result['relations'][] = ['relation' => $relation, 'entry' => normalize($entries)];
        }
        $result['theme'] = ['openings' => $result['opening_themes'], 'endings' => $result['ending_themes']];
        $result['external'] = $result['external_links'];
        $result['streaming'] = $result['streaming_links'];
        if (preg_match('/^(Winter|Spring|Summer|Fall) (\d{4})$/', $result['premiered'] ?? '', $m)) {
            $result['season'] = strtolower($m[1]);
            $result['year'] = (int)$m[2];
        }
        unset($result['related'], $result['opening_themes'], $result['ending_themes'], $result['external_links'], $result['streaming_links']);
    }
    if (isset($result['mal_id'], $result['title']) && !isset($result['aired'])) {
        $from = $result['start_date'] ?? $result['airing_start'] ?? null;
        $to = $result['end_date'] ?? null;
        if ($from && !str_contains($from, 'T')) $from = (new DateTimeImmutable($from))->format(DATE_ATOM);
        if ($to && !str_contains($to, 'T')) $to = (new DateTimeImmutable($to))->format(DATE_ATOM);
        $result['aired'] = ['from' => $from, 'to' => $to];
        $result['year'] = $from ? (int)substr($from, 0, 4) : null;
        if ($value instanceof Jikan\Model\Search\AnimeSearchListItem) {
            $result['status'] = !$from || $from > date(DATE_ATOM) ? 'Not yet aired' :
                (($result['airing'] ?? false) ? 'Currently Airing' : 'Finished Airing');
            $result['rating'] = $result['rated'];
        }
    }
    return $result;
}

try {
    $input = json_decode(stream_get_contents(STDIN), true, 64, JSON_THROW_ON_ERROR);
    if (($input['mode'] ?? '') === 'health') {
        $result = ['php' => PHP_VERSION, 'jikan' => Composer\InstalledVersions::getPrettyVersion('jikan-me/jikan')];
    } else {
        [$method, $request, $size] = endpoint($input['path'], $input['params'] ?? []);
        if ($input['mode'] === 'describe') {
            $result = ['url' => $request->getPath(), 'page_size' => $size];
        } else {
            if (($input['status'] ?? 200) === 200) {
                $document = new Symfony\Component\DomCrawler\Crawler($input['html']);
                if (!$document->filter('#content')->count()) {
                    throw new UnexpectedValueException('Expected a MyAnimeList content page, not a challenge or error page');
                }
            }
            // Exactly one supplied HTML response; no HTTP transport or arbitrary URL execution.
            $http = new MockHttpClient([new MockResponse($input['html'], [
                'http_code' => $input['status'] ?? 200,
                'response_headers' => ['content-type: text/html; charset=utf-8'],
            ])]);
            $model = (new MalClient($http))->$method($request);
            $data = match ($method) {
                'getAnimeGenres' => array_merge($model->getGenres(), $model->getExplicitGenres(), $model->getThemes(), $model->getDemographics()),
                'getSeasonal' => $model->getAnime(),
                'getAnimeCharactersAndStaff' => $model->getCharacters(),
                default => $size ? $model->getResults() : $model,
            };
            $result = ['data' => normalize($data), 'has_next_page' => $size && $model->hasNextPage()];
        }
    }
    echo json_encode($result, JSON_UNESCAPED_UNICODE | JSON_INVALID_UTF8_SUBSTITUTE | JSON_THROW_ON_ERROR);
} catch (Throwable $e) {
    fwrite(STDERR, get_class($e) . ': ' . $e->getMessage() . PHP_EOL);
    exit(1);
}
