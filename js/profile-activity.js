// Calendar dates follow the API's UTC day keys, independent of browser timezone.
export function dayDate(key) {
  return new Date(`${key}T12:00:00Z`);
}

export function calendarMonth(days, month) {
  const byDate = new Map(days.map(day => [day.d, day]));
  const first = dayDate(`${month}-01`);
  const shift = (first.getUTCDay() + 6) % 7;
  const cells = Array.from({ length: 42 }, (_, index) => {
    const date = new Date(first);
    date.setUTCDate(1 - shift + index);
    const key = date.toISOString().slice(0, 10);
    return { key, number: date.getUTCDate(), day: key.startsWith(month) ? byDate.get(key) : null,
      col: index % 7, row: Math.floor(index / 7) };
  });
  const adjacent = (delta) => {
    const date = new Date(first);
    date.setUTCMonth(date.getUTCMonth() + delta);
    const key = date.toISOString().slice(0, 7);
    return days.some(day => day.d.startsWith(key)) ? key : null;
  };
  return { cells, previous: adjacent(-1), next: adjacent(1) };
}

// Group adjacent same-day events of one title only: never reorder history or discard individual actions.
export function groupActivity(items) {
  const groups = [];
  for (const event of items) {
    const day = (event.at || '').slice(0, 10);
    const previous = groups[groups.length - 1];
    if (event.mal_id && previous && previous.day === day && previous.mal_id === event.mal_id) {
      previous.events.push(event);
      if (!previous.poster_url) previous.poster_url = event.poster_url;
    } else {
      groups.push({ ...event, day, events: [event] });
    }
  }
  // A single episode labels the whole group; several episodes are labelled per action.
  for (const group of groups) {
    group.episodes = [...new Set(group.events.map(event => event.episode_num).filter(Boolean))];
  }
  return groups;
}
