/*
 * Журнал щита AsoPlay в документе плеера (/player/asoplay-shield.js): очередь сообщений
 * в консоль, не чаще раза в 250 мс.
 *
 * Автор: Чепела Даниил Максимович.
 */
(function(){if(typeof window.__asoplayShieldLog==="function")return;var q=[],c={};try{["log","warn","error","info","debug"].forEach(function(m){var f=typeof console!=="undefined"&&console[m];if(typeof f==="function")c[m]=f.bind(console);});}catch(_){}function flush(){try{var x;while((x=q.shift())){var f=c[x.m]||c.info;if(typeof f==="function")f.apply(console,x.a);}}catch(_){}}function l(m,a){try{q.push({m:m,a:a});if(q.length>40)q.splice(0,q.length-40);setTimeout(flush,0);}catch(_){}}try{Object.defineProperty(window,"__asoplayShieldLog",{value:l,configurable:true});}catch(_){window.__asoplayShieldLog=l;}setInterval(flush,250);})();
