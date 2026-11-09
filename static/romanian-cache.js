// Saved readings have no expiry. Dates remain attached to every historical value.
const romanianMemory = new Map();
const romanianFiles = {
  '/api/inhga-danube': 'stations', '/api/inhga-forecast': 'rows',
  '/api/inhga-bulletin-forecast': 'bulletin'
};
function romanianDate(data) {
  if (data.data_date) return data.data_date;
  if (data.observation_date) return data.observation_date;
  const stationDate = (data.stations || []).map(s => s.date?.slice(0,10) || '').sort().pop();
  if (stationDate) return stationDate;
  const date = data.bulletin_date || data.bulletin?.bulletin_date;
  if (/^\d{2}\.\d{2}\.\d{4}$/.test(date || '')) return date.split('.').reverse().join('-');
  return date || '';
}
function usableRomanian(data, field) {
  if (data?.error || !Array.isArray(data?.[field]) || !data[field].length) return false;
  if (field === 'stations') return data.stations.some(s => s && s.water_stage_cm != null);
  return Array.isArray(data.columns) && data.rows.some(r =>
    Array.isArray(r?.values) && r.values.slice(1).some(v => v != null && v !== ''));
}
async function fetchRomanianData(path, field) {
  const key = 'danube-v1:' + path;
  let saved = romanianMemory.get(key);
  const consider = candidate => {
    if (!candidate || !Number.isFinite(candidate.saved) || !usableRomanian(candidate.data, field)) return;
    if (!saved || romanianDate(candidate.data) > romanianDate(saved.data) ||
        (romanianDate(candidate.data) === romanianDate(saved.data) && candidate.saved > saved.saved)) {
      saved = candidate;
    }
  };
  try { consider(JSON.parse(localStorage.getItem(key))); } catch {}
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 20000);
  try {
    const response = await fetch(path, {signal: controller.signal});
    if (!response.ok) throw new Error();
    const data = await response.json();
    if (!usableRomanian(data, field)) throw new Error();
    if (saved && romanianDate(saved.data) > romanianDate(data)) throw new Error();
    saved = {saved: data.savedAt || data.fetched_at || Date.now(), data};
    romanianMemory.set(key, saved);
    try { localStorage.setItem(key, JSON.stringify(saved)); } catch {}
    return data;
  } catch {
    // Bundled snapshots also serve new browsers and browsers with storage disabled.
    try {
      const response = await fetch('/static/romanian-data/' + romanianFiles[path] + '.json',
        {signal: AbortSignal.timeout(8000)});
      if (response.ok) consider(await response.json());
    } catch {}
    if (saved) {
      romanianMemory.set(key, saved);
      try { localStorage.setItem(key, JSON.stringify(saved)); } catch {}
      return {...saved.data, savedAt: saved.saved, stale: true};
    }
    throw new Error('Saved readings unavailable. The dashboard will retry automatically.');
  } finally { clearTimeout(timeout); }
}
function romanianNotice(data) {
  const date = romanianDate(data);
  const today = new Intl.DateTimeFormat('en-CA', {timeZone: 'Europe/Bucharest'}).format(new Date());
  if (!data.savedAt && !data.stale && (!date || date >= today)) return '';
  const text = 'Last available data' + (date ? ' · dated ' + date : '') +
    '. Showing the saved readings while updates are retried. Forecast dates remain as originally published.';
  return '<div role="status" class="romanian-notice">' + esc(text) + '</div>';
}
function romanianStatus(data, count) {
  const date = romanianDate(data);
  return count + ' stations' + (date ? ' · ' + date : '') +
    (data.savedAt || data.stale ? ' · saved copy' : '');
}
function displayRomanianStations(json) {
  danubeData = json;
  renderDanubeTable();
  updateMapStations();
  RomanianPlants.render();
  danubeSt.textContent = romanianStatus(json, json.stations.length);
  return json;
}
function displayRomanianForecast(json) {
  renderINHGAForecast(json);
  inhgaFcSt.textContent = romanianStatus(json, json.rows.length);
  inhgaForecasts = {};
  const labels = (json.columns || []).slice(2, -2).map(c => c.replace(/\s*\(.*\)/, '').split(' ').pop());
  json.rows.forEach(r => {
    inhgaForecasts[r.station] = {
      issued: r.forecast_date || json.bulletin_date, values: r.values,
      first_alert: r.first_alert, second_alert: r.second_alert, labels
    };
  });
  RomanianPlants.render();
  updateMapStations();
  return json;
}
let romanianRefreshPending;
function refreshRomanianPanels() {
  if (romanianRefreshPending) return romanianRefreshPending;
  romanianRefreshPending = Promise.all([
    fetchRomanianData('/api/inhga-danube', 'stations').then(displayRomanianStations).catch(() => {
      danubeSt.textContent = 'Update unavailable · retrying automatically';
    }),
    fetchRomanianData('/api/inhga-forecast', 'rows').then(displayRomanianForecast).catch(() => {
      inhgaFcSt.textContent = 'Update unavailable · retrying automatically';
    })
  ]).finally(() => { romanianRefreshPending = null; });
  return romanianRefreshPending;
}
