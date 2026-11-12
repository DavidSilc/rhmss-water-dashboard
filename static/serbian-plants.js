// Use the same plant list and measured station data as the map.
const SerbianPlants = (() => {
  const {node, link, metric, level, flow, value} = RomanianPlants.ui;
  const eps = 'https://www.eps.rs/lat/';
  const dlhe = eps + 'dlhe/Stranice/';
  const djerdap = eps + 'djerdap/Stranice/';
  const details = {
    'Đerdap I': {id: 'djerdap-1', units: '6 × 190 MW · Serbian side', source: 'https://www.eps.rs/eng/vesti/Pages/54-23.aspx',
      gauges: [['GOLUBAC', 'Reservoir gauge']], forecast: ['VELIKO GRADIŠTE'], danube: true},
    'Đerdap II': {id: 'djerdap-2', units: '10 × 27 MW · Serbian side', source: djerdap + 'he_djerdap2.aspx',
      gauges: [['PRAHOVO', 'Downstream gauge']], forecast: ['Gruia'], danube: true},
    'Bajina Bašta': {id: 'bajina-basta', units: '4 × 105 MW · Perućac reservoir', source: dlhe + 'HE-Bajina-Basta.aspx',
      gauges: [['BAJINA BAŠTA', 'Drina gauge']], forecast: ['BAJINA BAŠTA', 'RADALJ']},
    'RHE Bajina Bašta': {id: 'rhe-bajina-basta', units: '2 × 307 MW · pumped storage', source: dlhe + 'Proizvodni-kapaciteti.aspx',
      gauges: [['BAJINA BAŠTA', 'Lower basin context']], forecast: ['BAJINA BAŠTA', 'RADALJ'],
      note: 'Upper reservoir at Zaovine. Pumping and generation follow reservoir management and dispatch.'},
    'Zvornik': {id: 'zvornik', units: '4 units · Drina', source: 'https://www.eps.rs/eng/vesti/Pages/43-22.aspx',
      gauges: [['RADALJ', 'Downstream gauge'], ['BADOVINCI', 'Downstream gauge']], forecast: ['RADALJ']},
    'Potpeć': {id: 'potpec', units: '3 × 17 MW · Lim', source: dlhe + 'Proizvodnja.aspx',
      gauges: [['BRODAREVO', 'Upstream gauge'], ['PRIJEPOLJE', 'Upstream gauge']], forecast: ['PRIJEPOLJE', 'BRODAREVO']},
    'Bistrica': {id: 'bistrica', units: '2 units · Radoinja reservoir', source: dlhe + 'Proizvodnja.aspx',
      gauges: [['PRIBOJ', 'Regional downstream gauge']], forecast: ['PRIBOJ'],
      note: 'Water is diverted from the Uvac through Radoinja to the Lim.'},
    'Kokin Brod': {id: 'kokin-brod', units: '2 units · Zlatar reservoir', source: dlhe + 'Proizvodnja.aspx',
      gauges: [['PRIBOJ', 'Regional downstream gauge']], forecast: ['PRIBOJ'],
      note: 'Uvac cascade. Reservoir levels and plant intake flow are unavailable.'},
    'Uvac': {id: 'uvac', units: '1 unit · Uvac reservoir', source: dlhe + 'Proizvodnja.aspx',
      gauges: [['PRIBOJ', 'Regional downstream gauge']], forecast: ['PRIBOJ'],
      note: 'Uvac cascade. Reservoir levels and plant intake flow are unavailable.'},
    'Vlasina': {id: 'vlasina', units: 'Vrla I–IV · 4 plants / 10 units', source: eps + 'vesti/Stranice/biser-s-vlasine.aspx',
      gauges: [['SVOĐE', 'Regional Vlasina gauge'], ['VLASOTINCE', 'Regional Vlasina gauge']], forecast: ['SVOĐE', 'VLASOTINCE'],
      note: 'The Vrla cascade uses the Vlasina reservoir. The gauges show regional river conditions.'},
    'Pirot': {id: 'pirot', units: '2 × 40 MW · Zavoj reservoir', source: djerdap + 'he_pirot.aspx',
      gauges: [['IZATOVAC', 'Visočica upstream gauge'], ['PIROT', 'Nišava basin context']], forecast: ['PIROT', 'DIMITROVGRAD']},
    'Međuvršje': {id: 'medjuvrsje', units: '3 units · Zapadna Morava', source: dlhe + 'Proizvodnja.aspx',
      gauges: [['KRATOVSKA STENA', 'Upstream gauge'], ['ČAČAK', 'Downstream gauge']], forecast: ['KRATOVSKA STENA', 'ČAČAK']},
    'Ovčar Banja': {id: 'ovcar-banja', units: '2 units · Zapadna Morava', source: dlhe + 'Proizvodnja.aspx',
      gauges: [['KRATOVSKA STENA', 'Upstream gauge'], ['ČAČAK', 'Downstream gauge']], forecast: ['KRATOVSKA STENA', 'ČAČAK']}
  };
  const clock = new Intl.DateTimeFormat('en-GB', {timeZone: 'Europe/Belgrade',
    day: '2-digit', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit', timeZoneName: 'short'});
  const grid = document.getElementById('rs-plants');
  let energy = {}, pending, loaded = false;
  function stamp(t) { return Number.isFinite(t) ? clock.format(t * 1000) : ''; }
  function forecast(box, config) {
    const name = config.forecast.find(n => getForecastForStation(n, n === 'Gruia' ? 'INHGA' : 'RHMSS'));
    if (!name) {
      box.append(node('small', 'Forecast unavailable for the selected gauges.'));
      return;
    }
    const source = name === 'Gruia' ? 'INHGA' : 'RHMSS';
    const fc = getForecastForStation(name, source);
    const label = node('small', name + ' · water-level forecast (cm) · ' + source);
    const days = node('div', null, 'ro-days');
    fc.values.slice(1).forEach((v, index) => {
      const i = index + 1;
      const valid = v != null && v !== '' && Number.isFinite(Number(v));
      const previous = fc.values[i - 1];
      const delta = valid && previous != null && previous !== '' ? Number(v) - Number(previous) : 0;
      const day = node('div');
      day.append(node('small', fc.labels?.[i] || '—'), node('b', valid ? v : '—',
        delta > 0 ? 'forecast-trend-up' : delta < 0 ? 'forecast-trend-down' : 'forecast-trend-flat'));
      days.append(day);
    });
    box.append(label, days);
  }
  function render() {
    if (!grid) return;
    grid.replaceChildren(...PLANTS.map(plant => {
      const config = details[plant.name];
      const box = node('article', null, 'pg-card ro-card rs-card');
      box.id = 'rs-' + config.id;
      const top = node('div', null, 'pg-header');
      top.append(node('h3', plant.name));
      if (map && plantMarkers[plant.name]) {
        const button = node('button', 'Map');
        button.type = 'button';
        button.setAttribute('aria-label', 'Show ' + plant.name + ' on map');
        button.onclick = () => {
          document.getElementById('hydro-map').scrollIntoView({block: 'center'});
          map.setView([plant.lat, plant.lng], 13);
          plantMarkers[plant.name].openPopup();
        };
        top.append(button);
      }
      box.append(top, node('small', 'Hydropower · EPS · ' + plant.river));
      metric(box, 'Serbian rated capacity', value(plant.mw, 'MW'), config.units);
      const reading = energy.generation?.[plant.name];
      metric(box, 'Last reported generation', reading ? value(reading.mw, 'MW') : loaded ? 'Unavailable' : 'Loading…',
        reading ? stamp(reading.at) + ' · ENTSO-E / Energy-Charts' : 'Per-plant output is shown where published.');
      if (reading?.units) {
        box.append(node('small', reading.units.map(u => u.name + ' ' + value(u.mw, 'MW')).join(' · ')));
      }
      const notices = energy.notices?.plants?.[plant.name] || [];
      if (notices.length) {
        box.append(node('h4', 'Published unavailability'));
        notices.forEach(n => metric(box, n.unit || 'Reported unit', n.lost,
          n.period + ' · ' + n.status));
        box.append(link('EuroEnergy · checked ' + stamp(energy.notices.checked_at),
          'https://euroenergy.live/en/country/serbia'));
      }
      box.append(node('h4', 'River conditions'));
      if (config.danube) {
        const b = danubeData?.bulletin || {};
        flow(box, 'Baziaș upstream flow', b.bazias_flow_m3s, b.bazias_tendency);
        flow(box, 'Baziaș forecast flow', b.forecast_bazias_flow_m3s, b.forecast_bazias_tendency);
        if (b.bulletin_date) box.append(node('small', 'INHGA bulletin · ' + b.bulletin_date));
      }
      config.gauges.forEach(([name, description]) => {
        level(box, name, stationData, 'RHMSS · ' + description);
        const station = stationData.find(s => normalizeName(s.name) === normalizeName(name));
        if (Number.isFinite(station?.river_flow)) metric(box, name + ' river flow', value(station.river_flow, 'm³/s'));
      });
      forecast(box, config);
      if (config.note) box.append(node('p', config.note));
      const sources = node('small', null, 'ro-sources');
      sources.append(link('EPS · plant information', config.source), ' · ',
        link('RHMSS', 'https://www.hidmet.gov.rs/eng/hidrologija/izvestajne/index.php'));
      if (config.danube) sources.append(' · ', link('INHGA', danubeData?.bulletin?.bulletin_url || 'https://www.hidro.ro/'));
      if (reading) sources.append(' · ', link('Energy-Charts', 'https://energy-charts.info/charts/power/chart.htm?l=en&c=RS&source=hydro_pumped_storage_unit'));
      box.append(sources);
      return box;
    }));
  }
  function refresh() {
    if (pending) return pending;
    pending = fetch('/api/serbian-plants', {signal: AbortSignal.timeout(20000)}).then(r => {
      if (!r.ok) throw new Error();
      return r.json();
    }).then(data => { energy = data; }).catch(() => {
      // Dated readings remain visible if an update is unavailable.
    }).finally(() => { loaded = true; pending = null; render(); });
    return pending;
  }
  function init() {
    render();
    PLANTS.forEach(plant => {
      const popup = plantMarkers[plant.name]?.getPopup();
      if (popup) {
        popup.setContent(popup.getContent() + '<p><a href="#rs-' + details[plant.name].id + '">View plant readings</a></p>');
      }
    });
    refresh();
  }
  return {init, render, refresh};
})();
