// Romanian generation is reported separately from the Serbian plants at the same dams.
const RomanianPlants = (() => {
  const sites = [
    {id: 'portile-1', name: 'Porțile de Fier I', coords: [44.6733, 22.5320], capacity: '1,166.4 MW', machines: '6 × 194.4 MW'},
    {id: 'portile-2', name: 'Porțile de Fier II', coords: [44.3074, 22.5672], capacity: '251.2 MW', machines: '8 × 31.4 MW · main powerhouse'},
    {id: 'cernavoda', name: 'Cernavodă', coords: [44.322, 28.057], nuclear: true}
  ];
  const grid = document.getElementById('ro-plants');
  const markers = {};
  const powerGrid = 'https://www.powergrid.ro/';
  const charts = 'https://energy-charts.info/charts/power/chart.htm?l=en&c=RO&source=nuclear_unit';
  const capacityReport = 'https://cdn.hidroelectrica.ro/cdn/aga/2025/29_aprilie_2025/raport_anual/H2O_RO_Raport_FY-2024_merged.pdf';
  const number = new Intl.NumberFormat('en-GB', {maximumFractionDigits: 2});
  const clock = new Intl.DateTimeFormat('en-GB', {timeZone: 'Europe/Bucharest', day: '2-digit',
    month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit', timeZoneName: 'short'});
  let energy = {}, pending, energyLoaded = false, hydrologyRequested = false;
  let bulletinForecast, bulletinPending;
  function node(tag, value, cls) {
    const el = document.createElement(tag);
    if (value != null) el.textContent = value;
    if (cls) el.className = cls;
    return el;
  }
  function link(label, url) {
    const a = node('a', label);
    if (url && /^https?:\/\//i.test(url)) {
      a.href = url;
      a.target = '_blank';
      a.rel = 'noopener';
    }
    return a;
  }
  function stamp(t) { return Number.isFinite(t) && t > 0 ? clock.format(t * 1000) : ''; }
  function value(v, unit) { return Number.isFinite(v) ? number.format(v) + ' ' + unit : 'Unavailable'; }
  function metric(box, label, main, note = '') {
    const row = node('div', null, 'ro-metric');
    row.append(node('span', label), node('strong', main));
    if (note) row.append(node('small', note));
    box.append(row);
    return row;
  }
  function measured(box, label, reading) {
    metric(box, label, reading ? value(reading.mw, 'MW') : energyLoaded ? 'Unavailable' : 'Loading…', stamp(reading?.at));
  }
  function level(box, name, data, source) {
    const s = data?.find(x => normalizeName(x.name) === normalizeName(name));
    const row = metric(box, name + ' gauge', value(s?.water_stage_cm, 'cm'),
      [source, s?.date].filter(Boolean).join(' · '));
    if (Number.isFinite(s?.change_cm)) {
      const change = s.change_cm;
      const trend = node('em', (change > 0 ? '+' : '') + change + ' cm', change > 0 ? 'forecast-trend-up' :
        change < 0 ? 'forecast-trend-down' : 'forecast-trend-flat');
      row.querySelector('strong').append(' ', trend);
    }
  }
  function flow(box, label, v, trend) {
    const row = metric(box, label, value(v, 'm³/s'));
    if (trend) row.querySelector('strong').append(' ', node('em', fmtTendArrow(trend) + ' ' + trend,
      trend === 'rising' ? 'forecast-trend-up' : trend === 'falling' ? 'forecast-trend-down' : 'forecast-trend-flat'));
  }
  function hydro(box, site) {
    metric(box, 'Romanian installed capacity', site.capacity, site.machines);
    if (site.id === 'portile-1') measured(box, 'Last reported generation', energy.iron_gates_1);
    else metric(box, 'Plant generation', 'Unavailable', 'No separate output series in the connected sources.');
    const b = danubeData?.bulletin || {};
    box.append(node('h4', 'Danube flow outlook'));
    flow(box, 'Baziaș upstream flow', b.bazias_flow_m3s, b.bazias_tendency);
    flow(box, 'Baziaș forecast flow', b.forecast_bazias_flow_m3s, b.forecast_bazias_tendency);
    if (site.id === 'portile-1') level(box, 'Orșova', danubeData?.stations, 'INHGA');
    else {
      level(box, 'Gruia', danubeData?.stations, 'INHGA');
      level(box, 'Prahovo', stationData, 'RHMSS');
    }
    const foot = node('small', null, 'ro-sources');
    foot.append(link('INHGA' + (b.bulletin_date ? ' · ' + b.bulletin_date : ''), b.bulletin_url || 'https://www.hidro.ro/'),
      ' · ', link('Hidroelectrica', capacityReport), ' · ', link('PowerGrid', powerGrid));
    box.append(foot);
  }
  function nuclear(box) {
    measured(box, 'Total output · both units', energy.nuclear_total);
    for (const unit of ['1', '2']) {
      box.append(node('h4', 'Unit ' + unit));
      measured(box, 'Last reported net generation', energy.unit_mw?.[unit]);
      const n = energy.notices?.units[unit];
      if (n) metric(box, 'Reported unavailable', value(n.lost, 'MW'),
        'Scheduled outage end ' + stamp(n.to) + ' · Notice issued ' + stamp(n.at));
      else if (energyLoaded) box.append(node('small', energy.notices ?
        'No active outage listed as of ' + stamp(energy.notices.as_of) : 'Outage feed unavailable.'));
      const report = energy.reports?.[unit];
      if (report) {
        const p = node('p');
        p.append(link('SNN · ' + report.date, report.url), ' · ' + report.title);
        box.append(p);
      }
    }
    box.append(node('h4', 'Cooling-water conditions'));
    level(box, 'Cernavodă', danubeData?.stations, 'INHGA · local gauge zero');
    const afdj = getForecastForStation('Cernavoda', 'INHGA');
    const fc = bulletinForecast && (!afdj || bulletinForecast.issued > afdj.issued ||
      (bulletinForecast.issued === afdj.issued && bulletinForecast.values.filter(v => v !== '').length >=
        afdj.values.filter(v => v !== '').length)) ? bulletinForecast : afdj;
    if (fc && fc.values.slice(1).some(v => v !== '' && v != null)) {
      const section = node('div', null, 'ro-days');
      for (let i = 1; i < fc.values.length; i++) {
        const v = fc.values[i], prev = fc.values[i - 1];
        const valid = v != null && v !== '' && Number.isFinite(Number(v));
        const delta = valid && prev !== '' && prev != null ? Number(v) - Number(prev) : 0;
        const day = node('div');
        day.append(node('small', fc.labels[i]), node('b', valid ? number.format(Number(v)) : '—',
          delta > 0 ? 'forecast-trend-up' : delta < 0 ? 'forecast-trend-down' : 'forecast-trend-flat'));
        section.append(day);
      }
      box.append(node('small', 'Gauge forecast (cm) · issued ' + (fc.issued || 'date unavailable')), section);
    } else box.append(node('small', 'Gauge forecast unavailable.'));
    box.append(node('p', 'Intake basin level unavailable. The river gauge alone cannot establish a restart date.'));
    const foot = node('small', null, 'ro-sources');
    foot.append(link('ENTSO-E via Energy-Charts', charts), ' · ', link('PowerGrid', powerGrid), ' · ',
      link('SNN reports', 'https://nuclearelectrica.ro/ir/en/reports/current-reports/'));
    box.append(foot);
  }
  function render() {
    const cards = sites.map(site => {
      const box = node('article', null, 'pg-card ro-card');
      box.id = site.id;
      const top = node('div', null, 'pg-header');
      top.append(node('h3', site.name));
      if (markers[site.id]) {
        const button = node('button', 'Map');
        button.type = 'button';
        button.setAttribute('aria-label', 'Show ' + site.name + ' on map');
        button.onclick = () => {
          document.getElementById('hydro-map').scrollIntoView({block: 'center'});
          map.setView(site.coords, 13);
          markers[site.id].openPopup();
        };
        top.append(button);
      }
      box.append(top, node('small', site.nuclear ? 'Nuclear · Nuclearelectrica' : 'Hydropower · Hidroelectrica'));
      if (site.nuclear) nuclear(box); else hydro(box, site);
      if (!hydrologyRequested && !danubeData) box.append(node('small', 'Use Refresh to load river readings.'));
      return box;
    });
    grid.replaceChildren(...cards);
  }
  function init(map) {
    if (map) for (const site of sites) {
      const label = site.name + (site.nuclear ? ' · Units 1 & 2' : ' · Romania');
      const icon = L.divIcon({className: site.nuclear ? 'ro-marker ro-nuclear' : 'ro-marker',
        html: site.nuclear ? '☢' : '⚡', iconSize: [24,24], iconAnchor: [12,12]});
      const popup = node('div');
      const a = node('a', 'View plant readings');
      a.href = '#' + site.id;
      popup.append(node('b', label), node('br'), a);
      markers[site.id] = L.marker(site.coords, {icon, title: label, zIndexOffset: 1001}).addTo(map).bindPopup(popup);
    }
    render();
    loadEnergy();
  }
  function loadEnergy() {
    if (pending) return pending;
    pending = fetch('/api/romanian-plants').then(r => {
      if (!r.ok) throw new Error();
      return r.json();
    }).then(data => { energy = data; }).catch(() => { energy = {}; }).finally(() => {
      energyLoaded = true;
      pending = null;
      render();
    });
    return pending;
  }
  async function loadBulletin() {
    if (bulletinPending) return;
    bulletinPending = true;
    try {
      const data = await fetchRomanianData('/api/inhga-bulletin-forecast', 'rows');
      const r = data.rows?.find(r => normalizeName(r.station) === 'cernavoda');
      if (r?.values.slice(1).some(v => v !== '' && v != null)) {
        bulletinForecast = {issued: r.forecast_date, values: r.values,
          labels: data.columns.slice(2, -2).map(c => c.split(' ')[1])};
        render();
      }
    } catch { /* Keep the dated AFDJ forecast if the bulletin is unavailable. */ }
    finally { bulletinPending = false; }
  }
  return {init, render, refresh() { hydrologyRequested = true; loadBulletin(); return loadEnergy(); }};
})();
