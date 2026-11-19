import { glucoseValue, lineChart, stepsChart, eventChart } from './chart.mjs?v=20261118-study-timeline';

const $ = id => document.getElementById(id);
const state = { access: null, refresh: null, user: null, unlock: null, expiry: null, configured: false, scope: 'granted', patients: [], next: null, selected: null, overview: null, epoch: 0, request: null };
const sourceNames = { manual: '手工记录', report: '报告数据', public_dataset: '公开数据集（历史记录）', ohio_cgm: 'OhioT1DM · CGM' };
const contextNames = { fasting: '空腹', pre_meal: '餐前', post_meal: '餐后', bedtime: '睡前', night: '夜间', other: '其他' };
const dateTime = value => value ? new Date(value).toLocaleString('zh-CN', { hour12: false, month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }) : '—';
const text = (id, value) => { $(id).textContent = value; };
const show = (id, visible = true) => { $(id).hidden = !visible; };
function element(tag, className, value) { const node = document.createElement(tag); if (className) node.className = className; if (value !== undefined) node.textContent = String(value); return node; }
function notify(message = '') { text('notice', message); show('notice', !!message); }

function reset(message = '') {
  state.request?.abort(); state.epoch++;
  Object.assign(state, { access: null, refresh: null, user: null, unlock: null, expiry: null, scope: 'granted', patients: [], next: null, selected: null, overview: null });
  if ($('unlock-dialog').open) $('unlock-dialog').close();
  clearDetail(); $('patient-list').replaceChildren();
  show('workspace', false); show('login-view');
  text('login-error', message); $('password').value = ''; $('unlock-password').value = '';
}

async function api(path, { body, method = 'GET', signal } = {}) {
  const epoch = state.epoch;
  const headers = { Accept: 'application/json' };
  if (body !== undefined) headers['Content-Type'] = 'application/json';
  if (state.access) headers.Authorization = 'Bearer ' + state.access;
  if (state.unlock) headers['X-Clinical-Unlock'] = state.unlock;
  const response = await fetch(path, { method, headers, signal, cache: 'no-store', body: body === undefined ? undefined : JSON.stringify(body) });
  if (epoch !== state.epoch) throw new DOMException('账号会话已改变', 'AbortError');
  const data = response.status === 204 ? null : await response.json();
  if (!response.ok) {
    if (response.status === 401 && state.access) reset('登录已失效，请重新登录。');
    const error = new Error(typeof data?.detail === 'string' ? data.detail : '请求未完成，请检查输入后重试');
    error.status = response.status;
    throw error;
  }
  return data;
}

function clearDetail(keepNavigation = false) {
  glucoseContextRequest?.abort();
  $('glucose-context-events').replaceChildren();
  if ($('glucose-record-dialog').open) $('glucose-record-dialog').close();
  state.overview = null;
  state.olderGlucose = [];
  state.moreGlucose = false;
  text('glucose-records-status', '');
  show('demo-notice', false); show('public-notice', false); show('study-card', false); show('study-notice', false);
  show('clinical-content', false);
  if (!keepNavigation) {
    show('feature-links', false);
    show('feature-empty');
  }
  $('glucose-chart').replaceChildren(); $('wearable-sources').replaceChildren();
  $('glucose-records').replaceChildren(); $('profile-fields').replaceChildren(); $('health-facts').replaceChildren();
}

function setScope(scope) {
  state.request?.abort(); state.selected = null; clearDetail();
  state.scope = scope;
  $('scope-granted').setAttribute('aria-pressed', String(scope === 'granted'));
  $('scope-all').setAttribute('aria-pressed', String(scope === 'all'));
  text('scope-description', scope === 'all' ? '统一密码解锁 · 全体患者只读' : '查看已授权临床患者和研究样本');
  text('access-badge', scope === 'all' ? '统一密码 · 全体患者只读' : '个人授权 · 只读');
  show('patient-detail', false); show('no-patient');
  show('scope-expiry', scope === 'all');
  if (scope === 'all') text('scope-expiry', state.expiry ? `解锁至 ${dateTime(state.expiry)} · 再点一次可锁定` : '已解锁 · 无定时到期 · 再点一次可锁定');
}

async function loadPatients(append = false) {
  const epoch = state.epoch, scope = state.scope, search = $('patient-search').value.trim(), dataset = '';
  if (!append) { state.patients = []; state.next = null; $('patient-list').replaceChildren(element('p', 'list-message', '正在读取患者…')); }
  show('more-patients', false);
  try {
    const params = new URLSearchParams({ scope, search, dataset, limit: '50' });
    if (append && state.next) params.set('after', state.next);
    const data = await api('/api/v1/clinical/patients?' + params);
    if (epoch !== state.epoch || scope !== state.scope || search !== $('patient-search').value.trim()) return;
    state.patients = append ? [...state.patients, ...data.patients] : data.patients;
    state.next = data.next_after;
    text('patient-count', data.total);
    renderPatients(); show('more-patients', !!state.next);
    show('example-patients', scope === 'granted');
    if (scope === 'granted') await loadExamples();
  } catch (error) {
    if (error.name === 'AbortError' || !state.access) return;
    $('patient-list').replaceChildren(element('p', 'list-message', error.message));
    if (scope === 'all' && error.status === 403) await expireScope(error.message);
  }
}

function patientDisplayName(patient) {
  const ohio = /^ohio_(2018|2020)_(\d+)$/.exec(patient.username);
  if (ohio) return `研究数据：Ohio ${ohio[1]} · ${ohio[2]}`;
  const uci = /^uci_diabetes_(\d{2})$/.exec(patient.username);
  if (uci) return `研究数据：UCI Diabetes · ${uci[1]}`;
  return (patient.full_name || '未填写姓名').replaceAll(' · ', '：');
}

function renderPatients() {
  const rows = state.patients.map(patient => {
    const button = element('button', 'patient-row' + (patient.id === state.selected ? ' selected' : ''));
    button.type = 'button';
    const name = patientDisplayName(patient);
    const copy = element('span', 'row-copy');
    copy.append(element('strong', '', name), element('span', 'mono', patient.id));
    button.append(element('span', 'avatar', name.slice(0, 1)), copy);
    button.setAttribute('aria-label', '查看患者 ' + name + ' ' + patient.id);
    button.addEventListener('click', () => selectPatient(patient));
    return button;
  });
  $('patient-list').replaceChildren(...(rows.length ? rows : [element('p', 'list-message', $('patient-search').value ? '没有匹配的患者' : state.scope === 'all' ? '暂无患者账号' : '暂无已授权临床患者。')]));
}

function localDate(date) { return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`; }
function quickRange(days) {
  const end = new Date(), start = new Date(); start.setDate(end.getDate() - days + 1);
  $('range-start').value = localDate(start); $('range-end').value = localDate(end);
  document.querySelectorAll('[data-days]').forEach(button => button.classList.toggle('selected', Number(button.dataset.days) === days));
}
function range() {
  const start = new Date($('range-start').value + 'T00:00:00');
  const end = new Date($('range-end').value + 'T00:00:00'); end.setDate(end.getDate() + 1);
  if (!Number.isFinite(start.getTime()) || !Number.isFinite(end.getTime()) || end <= start || end - start > 90 * 86400000) throw new Error('请选择不超过 90 天的有效时间范围');
  return { start: start.toISOString(), end: end.toISOString() };
}

async function selectPatient(patient, scope = state.scope) {
  if (state.selectedScope === 'examples' && scope !== 'examples') quickRange(14);
  state.selectedScope = scope;
  $('study-filter').value = '';
  state.feature = 'glucose-visualization';
  state.selected = patient.id; clearDetail(); renderPatients();
  text('access-badge', scope === 'examples' ? '研究样本 · 直接查看' : scope === 'all' ? '统一密码 · 只读' : '个人授权 · 只读');
  text('patient-name', patientDisplayName(patient)); text('patient-id', '账号 ID · ' + patient.id);
  show('no-patient', false); show('patient-detail');
  await loadOverview(true);
  if (state.selected === patient.id) scrollToPatient();
}

async function loadOverview(initial = false) {
  if (!state.selected) return;
  state.request?.abort(); const controller = new AbortController(); state.request = controller;
  const id = state.selected, scope = state.selectedScope;
  clearDetail(true); text('detail-status', '正在读取患者记录…');
  try {
    const params = new URLSearchParams({ ...range(), scope });
    const data = await api(`/api/v1/clinical/patients/${encodeURIComponent(id)}/overview?${params}`, { signal: controller.signal });
    if (controller.signal.aborted || id !== state.selected || scope !== state.selectedScope) return;
    if (initial === true && data.study) {
      const latest = new Date(new Date(data.study.period_end).getTime() - (data.study.dataset === 'OhioT1DM' ? 300000 : 1));
      document.querySelectorAll('[data-days]').forEach(button => button.classList.remove('selected'));
      $('range-start').value = localDate(latest); $('range-end').value = localDate(latest);
      return await loadOverview();
    }
    state.overview = data;
    state.moreGlucose = data.glucose.truncated;
    renderOverview();
    text('detail-status', '');
    show('clinical-content');
    show('feature-links');
    show('feature-empty', false);
  } catch (error) {
    if (error.name === 'AbortError' || !state.access) return;
    clearDetail(); text('detail-status', error.message);
    if (error.status === 403) {
      if (scope === 'all') await expireScope(error.message);
      else { notify('该患者当前不可访问，已清除页面中的数据。'); await loadPatients(); }
    }
  }
}

function renderOverview() {
  const data = state.overview; if (!data) return;
  // Explicit demonstration account, never inferred from a patient's health values.
  const demo = data.patient.username === 'antang_demo_patient';
  show('demo-notice', demo); show('public-notice', data.patient.username.startsWith('uci_diabetes_')); text('record-legend', demo ? '模拟演示记录' : '已保存记录');
  show('study-notice', !!data.study);
  if (data.study) {
    const notice = data.study.dataset === 'OhioT1DM' ? '匿名研究记录；日期已由原数据集脱敏平移，原时区未提供，以下按浏览器时区显示。' : data.study.provenance.notice;
    text('study-notice', `${data.study.dataset} · ${notice} 数据范围：${localDate(new Date(data.study.period_start))} 至 ${localDate(new Date(new Date(data.study.period_end).getTime()-1))}。`);
  }
  const unit = $('glucose-unit').value, label = unit === 'mg' ? 'mg/dL' : 'mmol/L';
  const value = n => n === null || n === undefined ? '—' : glucoseValue(n, unit).toFixed(unit === 'mg' ? 0 : 1);
  const points = data.glucose.points, summary = data.glucose.summary;
  document.querySelectorAll('.unit-text').forEach(node => { node.textContent = label; });
  text('glucose-count', summary.count.toLocaleString()); text('glucose-average', value(summary.average_mg_dl));
  text('glucose-range', `${value(summary.minimum_mg_dl)} / ${value(summary.maximum_mg_dl)}`);
  text('glucose-latest', value(points.at(-1)?.value_mg_dl)); text('glucose-latest-time', points.length ? dateTime(points.at(-1).measured_at) : '所选时间范围内尚无记录');
  lineChart($('glucose-chart'), points.map(point => ({ time: point.measured_at, value: glucoseValue(point.value_mg_dl, unit), source: point.source, reading: point, label: `${demo ? "模拟演示 · " : ""}${sourceNames[point.source] || point.source} · ${contextNames[point.context] || point.context}` })), { start: data.period_start, end: data.period_end, unit: label, simulated: demo, onActivate: point => showGlucoseRecord(point.reading), gapHours: data.study?.dataset === 'OhioT1DM' ? 0.25 : 6, reference: $('show-reference').checked ? [glucoseValue(70, unit), glucoseValue(180, unit)] : null });
  text('glucose-note', data.glucose.truncated ? `该范围共 ${summary.count} 条记录，曲线展示最新 ${points.length} 条；请缩短范围查看更早细节。上方统计按全部记录计算。` : `共 ${summary.count} 条有效血糖记录，已撤回记录不参与曲线与统计。时间按当前浏览器时区显示。`);
  text('sampling-note', data.study?.dataset === 'OhioT1DM' ? 'CGM 原始采样约每 5 分钟一次；超过 15 分钟的缺口断开，不插值。数据集日期已脱敏平移，可能晚于当前日期，不代表实时测量。' : '仅连接同来源、间隔不超过 6 小时的相邻记录；离散测量不能代表连续血糖。');
  renderStudy(data);
  show('study-link', !!data.study && data.study.dataset !== 'UCI Diabetes');
  text('study-link', data.study?.dataset === 'CGM PDF' ? 'CGM 原报告摘要' : '胰岛素与饮食');
  show('wearable-link', data.wearable.sources.length > 0);
  renderGlucoseRecords();
  show('wearable-heading', data.wearable.sources.length > 0); show('wearable-sources', data.wearable.sources.length > 0);
  renderWearable(data); renderProfile(data.profile);
  const dataRows = [['已保存血糖记录', summary.count ? `该时段 ${summary.count} 条` : '该时段暂无记录'], ['血糖设备自动同步', '尚未接入'], ['手环已同步数据', data.wearable.sources.length ? `${data.wearable.sources.length} 个独立来源` : '该时段暂无记录']];
  text('data-source-note', data.study?.dataset === 'OhioT1DM' ? '已导入 OhioT1DM CGM、胰岛素及饮食研究记录；未接入患者血糖设备实时同步。' : data.study?.dataset === 'CGM PDF' ? '本样本只有原报告摘要，没有逐点血糖数据；摘要不是本系统根据采样重新计算的结果。' : '当前显示已保存记录，血糖设备自动同步尚未接入。');
  renderFeature();
  $('data-status').replaceChildren(...dataRows.map(([name, status]) => { const row = element('div', 'data-row'); row.append(element('span', '', name), element('span', '', status)); return row; }));
}

const glucoseRecordTime = new Intl.DateTimeFormat('zh-CN', {
  year: 'numeric', month: 'numeric', day: 'numeric',
  hour: 'numeric', minute: '2-digit', second: '2-digit', hour12: false,
});

function glucoseRecordCells(point) {
  const unit = $('glucose-unit').value;
  return [
    glucoseRecordTime.format(new Date(point.measured_at)),
    glucoseValue(point.value_mg_dl, unit).toFixed(unit === 'mg' ? 0 : 2),
    contextNames[point.context] || point.context,
    sourceNames[point.source] || point.source,
    point.note || '—',
  ];
}

function showGlucoseRecord(point) {
  const dialog = $('glucose-record-dialog');
  const unit = $('glucose-unit').value === 'mg' ? 'mg/dL' : 'mmol/L';
  const labels = ['测量时间', '血糖 · ' + unit, '场景', '来源', '备注'];
  $('glucose-record-fields').replaceChildren(...glucoseRecordCells(point).map((value, index) => {
    const field = element('div');
    field.append(element('dt', '', labels[index]), element('dd', '', value));
    return field;
  }));
  if (!dialog.open) dialog.showModal();
  $('glucose-dialog-body').scrollTop = 0;
  void loadGlucoseContext(point);
}

// Scroll the record even if the pointer is over the modal header or its backdrop.
document.addEventListener('wheel', event => {
  if (!$('glucose-record-dialog').open || event.ctrlKey || !event.deltaY) return;
  const body = $('glucose-dialog-body');
  if (body.contains(event.target)) return;
  event.preventDefault();
  const mode = event.deltaMode;
  const scale = mode === WheelEvent.DOM_DELTA_PAGE ? body.clientHeight
    : mode === WheelEvent.DOM_DELTA_LINE ? 16 : 1;
  body.scrollTop += event.deltaY * scale;
}, {capture: true, passive: false});

let glucoseContextRequest;
$('glucose-record-dialog').addEventListener('close', () => {
  if (!$('glucose-record-dialog').open) glucoseContextRequest?.abort();
});

async function loadGlucoseContext(point) {
  glucoseContextRequest?.abort();
  const list = $('glucose-context-events');
  list.replaceChildren();
  if (state.overview?.study?.dataset !== 'OhioT1DM') {
    text('glucose-context-status', '此患者暂无可关联的胰岛素与饮食数据。');
    return;
  }

  const controller = new AbortController();
  glucoseContextRequest = controller;
  text('glucose-context-status', '正在查找附近记录…');
  const patient = encodeURIComponent(state.selected);
  const scope = encodeURIComponent(state.selectedScope);
  try {
    const events = await api(`/api/v1/clinical/patients/${patient}/glucose-records/${point.id}/context?scope=${scope}`,
      {signal: controller.signal});
    if (controller.signal.aborted || !$('glucose-record-dialog').open) return;
    if (!events.length) {
      text('glucose-context-status', '这个时段没有可关联的胰岛素或饮食记录。');
      return;
    }

    const sampleTime = Date.parse(point.measured_at);
    const activeAtSample = event => Date.parse(event.measured_at) <= sampleTime && Date.parse(event.ended_at) > sampleTime;
    const hasTemporaryRate = events.some(event => event.kind === 'temp_basal' && activeAtSample(event));
    for (const event of events) {
      const label = studyKinds[event.kind];
      let suffix = '';
      if (activeAtSample(event)) {
        suffix = event.kind === 'basal' && hasTemporaryRate ? '（当时由临时基础速率覆盖）' : '（采样时处于该记录时段）';
      }
      const times = glucoseRecordTime.format(new Date(event.measured_at)) +
        (event.ended_at ? ' 至 ' + glucoseRecordTime.format(new Date(event.ended_at)) : '');
      const item = element('li');
      item.append(element('strong', '', `${label} · ${event.value} ${event.unit}${suffix}`), element('span', '', times));
      list.append(item);
    }
    text('glucose-context-status', '按时间对照记录，不能单凭时间接近判断血糖变化的原因。');
  } catch (error) {
    if (controller.signal.aborted || !$('glucose-record-dialog').open) return;
    if (error.status === 403 || error.status === 404) {
      $('glucose-record-dialog').close();
      await loadOverview();
    } else text('glucose-context-status', '关联记录加载失败，请关闭后重新点选。');
  }
}

function renderGlucoseRecords() {
  const {glucose} = state.overview;
  const points = [...glucose.points].reverse().concat(state.olderGlucose);
  const rows = document.createDocumentFragment();
  for (const point of points) {
    const row = element('tr');
    for (const value of glucoseRecordCells(point)) row.append(element('td', '', value));
    rows.append(row);
  }
  if (!points.length) {
    const row = element('tr');
    const cell = element('td', '', '此时间范围内暂无有效血糖记录');
    cell.colSpan = 5;
    row.append(cell);
    rows.append(row);
  }
  text('value-heading', '血糖 · ' + ($('glucose-unit').value === 'mg' ? 'mg/dL' : 'mmol/L'));
  $('glucose-records').replaceChildren(rows);
  text('records-caption', state.moreGlucose ? `已显示 ${points.length} 条 / 共 ${glucose.summary.count} 条` : `共 ${points.length} 条`);
  show('more-glucose-records', state.moreGlucose);
}

$('more-glucose-records').addEventListener('click', async event => {
  const overview = state.overview;
  if (!overview || !state.moreGlucose) return;
  const last = state.olderGlucose.at(-1) || overview.glucose.points[0];
  const button = event.currentTarget;
  button.disabled = true;
  text('glucose-records-status', '正在加载更早记录…');
  try {
    const params = new URLSearchParams({
      scope: state.selectedScope,
      start: overview.period_start,
      end: overview.period_end,
      before_time: last.measured_at,
      before_id: last.id,
    });
    const data = await api(`/api/v1/clinical/patients/${encodeURIComponent(state.selected)}/glucose-records?${params}`,
      {signal: state.request?.signal});
    if (state.overview !== overview) return;
    state.olderGlucose.push(...data.points);
    state.moreGlucose = data.has_more;
    renderGlucoseRecords();
    text('glucose-records-status', '');
  } catch (error) {
    if (error.name === 'AbortError' || state.overview !== overview) return;
    if (error.status === 403) {
      if (state.selectedScope === 'all') await expireScope(error.message);
      else { clearDetail(); text('detail-status', '该患者当前不可访问。'); }
    } else text('glucose-records-status', error.message);
  } finally {
    button.disabled = false;
  }
});

function renderProfile(profile) {
  const personal = profile.personal_profile;
  const fields = [['性别', personal.sex], ['年龄', personal.age_years === null ? null : `${personal.age_years} 岁`], ['身高', personal.height_cm === null ? null : `${personal.height_cm} cm`], ['体重', personal.weight_kg === null ? null : `${personal.weight_kg} kg`], ['居住地区', personal.resident_area], ['作息', personal.schedule_type]];
  $('profile-fields').replaceChildren(...fields.map(([key, value]) => { const item = element('div'); item.append(element('dt', '', key), element('dd', '', value ?? '未填写')); return item; }));
  $('health-facts').replaceChildren(...(profile.health_facts.length ? profile.health_facts.map(fact => element('p', '', fact.statement)) : [element('p', '', '暂无已维护的健康事实')]));
}

function renderWearable(data) {
  const demo = data.patient.username === 'antang_demo_patient';
  const cards = data.wearable.sources.map(source => {
    const card = element('article', 'card'), head = element('div', 'source-head'), name = element('div');
    name.append(element('h3', '', demo ? '模拟手环记录' : source.source_package === 'nodomain.freeyourgadget.gadgetbridge' ? 'Gadgetbridge 手环记录' : 'Zepp / Health Connect 记录'), element('small', '', `最近观测 ${dateTime(source.latest_observed_at)}`));
    head.append(name, element('span', 'tag subtle', source.device?.model || '已同步观测')); card.append(head);
    const totals = source.totals, metrics = element('div', 'wearable-metrics');
    for (const [label, value, detail] of [['累计步数', totals.steps.toLocaleString(), '该范围内已同步步数'], ['平均心率', totals.heart_rate_average === null ? '—' : `${totals.heart_rate_average} bpm`, `${totals.heart_rate_samples} 个${demo ? "模拟" : "记录"}样本`], ['睡眠记录总时长', totals.sleep_sessions ? `${(totals.sleep_minutes / 60).toFixed(1)} h` : '—', `${totals.sleep_sessions} 段睡眠记录`], ['平均血氧', totals.oxygen_saturation_average === null ? '—' : `${totals.oxygen_saturation_average}%`, `${totals.oxygen_saturation_samples} 次记录`]]) {
      const metric = element('div'); metric.append(element('span', '', label), element('strong', '', value), element('small', '', detail)); metrics.append(metric);
    }
    card.append(metrics);
    const plots = element('div', 'wearable-plots'), heart = element('div'), steps = element('div');
    const heartPlot = element('div', 'chart'), stepsPlot = element('div', 'chart');
    heart.append(element('h4', '', demo ? '心率 · 模拟样本' : '心率 · 最近的记录样本'), heartPlot); steps.append(element('h4', '', '每日步数 · 已同步日期'), stepsPlot); plots.append(heart, steps); card.append(plots);
    lineChart(heartPlot, source.heart_rate_trend.map(point => ({ time: point.observed_at, value: point.beats_per_minute, source: source.source_package })), { start: data.period_start, end: data.period_end, unit: 'bpm', simulated: demo, compact: true, gapHours: 1 });
    stepsChart(stepsPlot, source.daily, data.period_start, data.period_end);
    card.append(element('p', 'caption', demo ? '以上心率、步数、睡眠和血氧均为人工编造的演示数据，不代表真实设备测量。' : '心率曲线最多展示末尾 240 个记录样本，上方统计来自该范围全部有效样本。不同数据来源单独显示，避免重复累计。手环不提供此处的血糖测量值。'));
    return card;
  });
  $('wearable-sources').replaceChildren(...(cards.length ? cards : [element('div', 'card chart-empty', '该时间范围内没有已同步的手环记录')]));
}

async function expireScope(message) {
  state.unlock = null; state.expiry = null; setScope('granted'); notify(message); await loadPatients();
}

$('login-form').addEventListener('submit', async event => {
  event.preventDefault(); const button = event.submitter; button.disabled = true; text('login-error', '');
  try {
    const data = await api('/api/v1/auth/login', { method: 'POST', body: { username: $('username').value.trim(), password: $('password').value } });
    if (!['clinician', 'researcher'].includes(data.user.role)) { state.access = data.access_token; await api('/api/v1/auth/logout', { method: 'POST', body: { refresh_token: data.refresh_token } }); reset(); throw new Error('此账号不是已审核的医护或研发管理账号，请使用手机端。'); }
    Object.assign(state, { access: data.access_token, refresh: data.refresh_token, user: data.user });
    $('password').value = ''; show('login-view', false); show('workspace'); notify('');
    text('account-name', data.user.full_name || data.user.username); text('role-label', data.user.role === 'researcher' ? '研发管理' : '医护');
    const status = await api('/api/v1/clinical/access'); state.configured = status.configured;
    document.querySelectorAll('.patient-panel details').forEach(group => { group.open = false; });
    const admin = data.user.role === 'researcher'; show('feature-nav', data.user.role === 'clinician'); show('admin-view', admin); show('clinical-view', !admin);
    if (admin) text('password-config-state', status.configured ? '统一密码已设置，可在此修改' : '尚未设置统一密码');
    else { setScope('granted'); quickRange(14); await loadPatients(); }
  } catch (error) { if (!state.access) text('login-error', error.message); else notify(error.message); }
  finally { button.disabled = false; }
});

$('logout').addEventListener('click', async () => {
  let failure = '';
  try { await api('/api/v1/auth/logout', { method: 'POST', body: { refresh_token: state.refresh } }); }
  catch (error) { failure = '本机已退出，但服务端会话注销未确认：' + error.message; }
  finally { reset(failure); }
});
$('scope-granted').addEventListener('click', async () => { if (state.unlock) { try { await api('/api/v1/clinical/access/lock', { method: 'POST' }); } catch (error) { notify(error.message); } } state.unlock = null; state.expiry = null; setScope('granted'); await loadPatients(); });
$('scope-all').addEventListener('click', async () => {
  if (state.scope === 'all') { $('scope-granted').click(); return; }
  text('unlock-error', ''); $('unlock-password').value = '';
  if (!state.configured) { notify('统一查看密码尚未设置，请联系研发管理人员。'); return; }
  $('unlock-dialog').showModal(); $('unlock-password').focus();
});
$('cancel-unlock').addEventListener('click', () => { $('unlock-dialog').close(); $('unlock-password').value = ''; });
$('unlock-dialog').addEventListener('close', () => { $('unlock-password').value = ''; });
$('unlock-form').addEventListener('submit', async event => {
  event.preventDefault(); event.submitter.disabled = true; text('unlock-error', '');
  try {
    const result = await api('/api/v1/clinical/access/unlock', { method: 'POST', body: { password: $('unlock-password').value } });
    state.unlock = result.token; state.expiry = result.expires_at;
    $('unlock-dialog').close(); setScope('all'); notify('已解锁全部患者只读视图，访问将记录到你的个人账号。'); await loadPatients();
  } catch (error) { text('unlock-error', error.message); }
  finally { $('unlock-password').value = ''; event.submitter.disabled = false; }
});
$('shared-password-form').addEventListener('submit', async event => {
  event.preventDefault(); const button = event.submitter;
  if ($('new-shared-password').value !== $('confirm-shared-password').value) { text('admin-message', '两次输入的统一密码不一致'); return; }
  button.disabled = true; text('admin-message', '');
  try {
    await api('/api/v1/admin/clinical-access-password', { method: 'PUT', body: { account_password: $('admin-account-password').value, new_password: $('new-shared-password').value } });
    state.configured = true; text('password-config-state', '统一密码已设置，可在此修改'); text('admin-message', '保存成功。所有已解锁会话已失效，医护需要重新验证。'); event.target.reset();
  } catch (error) { text('admin-message', error.message); }
  finally { $('admin-account-password').value = ''; button.disabled = false; }
});
$('search-form').addEventListener('submit', event => { event.preventDefault(); state.request?.abort(); clearDetail(); state.selected = null; show('patient-detail', false); show('no-patient'); void loadPatients(); });
$('more-patients').addEventListener('click', async event => { event.target.disabled = true; await loadPatients(true); event.target.disabled = false; });
document.querySelectorAll('[data-days]').forEach(button => button.addEventListener('click', () => { quickRange(Number(button.dataset.days)); void loadOverview(); }));
$('range-form').addEventListener('submit', event => { event.preventDefault(); document.querySelectorAll('[data-days]').forEach(button => button.classList.remove('selected')); void loadOverview(); });
$('refresh').addEventListener('click', () => void loadOverview());
$('glucose-unit').addEventListener('change', renderOverview);
$('show-reference').addEventListener('change', renderOverview);

setInterval(async () => {
  if (!state.access || state.scope !== 'all') return;
  if (state.expiry && new Date(state.expiry).getTime() <= Date.now()) { await expireScope('全部患者视图已到期，请重新验证统一密码。'); return; }
  try { const status = await api('/api/v1/clinical/access'); if (!status.unlocked) await expireScope('统一查看权限已失效，页面中的患者数据已清除。'); }
  catch (error) { if (state.access) await expireScope('无法确认全部患者查看权限，已锁定该视图。' + error.message); }
}, 20000);
document.addEventListener('visibilitychange', () => { if (document.visibilityState === 'visible' && state.access && state.selected) void loadOverview(); });

const studyKinds = {
  bolus: '追加胰岛素',
  basal: '基础输注',
  temp_basal: '临时基础输注',
  meal: '饮食碳水',
  finger_stick: '指尖血糖',
};

function renderStudyRecords(study) {
  const kind = $('study-filter').value;
  const events = kind ? study.events.filter(event => event.kind === kind) : study.events;
  const count = `显示 ${events.length} 条`;
  const limit = study.events_truncated ? `。所选时段共 ${study.event_count} 条，当前筛选基于最新 ${study.events.length} 条，可缩短时间范围查看更早记录。` : '';
  text('study-count', count + limit);
  const rows = [...events].reverse().map(event => {
    const row = element('tr');
    for (const value of [
      dateTime(event.measured_at), dateTime(event.ended_at),
      studyKinds[event.kind] || event.kind, `${event.value} ${event.unit}`,
    ]) row.append(element('td', '', value));
    return row;
  });
  if (!rows.length) {
    const row = element('tr');
    const cell = element('td', '', '当前时段暂无此类记录');
    cell.colSpan = 4;
    row.append(cell);
    rows.push(row);
  }
  $('study-events').replaceChildren(...rows);
}

$('study-filter').addEventListener('change', () => {
  if (state.overview?.study) renderStudyRecords(state.overview.study);
});

function renderStudy(data) {
  const study = data.study; show('study-card', !!study && study.dataset !== 'UCI Diabetes'); if (!study || study.dataset === 'UCI Diabetes') return;
  text('study-title', study.dataset === 'CGM PDF' ? 'CGM 原报告摘要' : '胰岛素与饮食记录');
  const report = study.provenance.report;
  show('report-metrics', !!report); show('study-events-area', !report);
  if (report) {
    const labels = {mean_glucose:'报告平均葡萄糖',sd:'报告标准差',cv:'报告变异系数',estimated_hba1c:'报告估算 HbA1c'};
    $('report-metrics').replaceChildren(...Object.entries(report.metrics).map(([key,value]) => element('p','',`${labels[key]}：${value}`)),element('p','caption',`来源：${report.file}，${report.pages} 页。报告范围 ${report.start} 至 ${report.end}。未从 AGP 图或每日曲线反推采样；原报告参考区间可能因人群而异。`));
    return;
  }
  renderStudyRecords(study);
  eventChart($('study-chart'), study.events, data.period_start, data.period_end);
}

async function loadExamples() {
  const epoch = state.epoch;
  for (const dataset of ['uci', 'ohio']) {
    const container = $('examples-' + dataset);
    container.replaceChildren(element('p', 'list-message', '正在读取…'));
    try {
      const patients = [];
      let after = null;
      do {
        const params = new URLSearchParams({scope: 'examples', dataset, limit: '100'});
        if (after) params.set('after', after);
        const page = await api('/api/v1/clinical/patients?' + params);
        if (epoch !== state.epoch) return;
        patients.push(...page.patients);
        after = page.next_after;
      } while (after);
      container.replaceChildren(...patients.map(patient => {
        const button = element('button', 'patient-row', patientDisplayName(patient));
        button.type = 'button';
        button.addEventListener('click', () => {
          document.querySelectorAll('.example-tree .patient-row').forEach(row => row.classList.remove('selected'));
          button.classList.add('selected');
          void selectPatient(patient, 'examples');
        });
        return button;
      }));
      if (!patients.length) container.append(element('p', 'list-message', '暂无已导入记录'));
    } catch (error) {
      if (epoch === state.epoch) container.replaceChildren(element('p', 'list-message', error.message));
    }
  }
}

const featureButtons = [...$('feature-links').querySelectorAll('[data-feature]')];
const extraPanels = {
  'glucose-visualization': [document.querySelector('.metric-grid'), $('glucose-records-section')],
  'wearable-heading': [$('wearable-sources')],
};

function renderFeature() {
  const selected = featureButtons.find(button => !button.hidden && button.dataset.feature === state.feature);
  state.feature = (selected || featureButtons[0]).dataset.feature;
  for (const button of featureButtons) {
    const id = button.dataset.feature;
    const active = id === state.feature;
    button.setAttribute('aria-pressed', String(active));
    show(id, active);
    for (const panel of extraPanels[id] || []) panel.hidden = !active;
  }
  if (state.feature === 'profile-section') $('profile-section').open = true;
}

function scrollToPatient() {
  const heading = $('patient-heading-anchor').getBoundingClientRect();
  const header = $('workspace-header').getBoundingClientRect();
  const navHeight = $('feature-nav').getBoundingClientRect().height;
  window.scrollBy({top: heading.top - header.bottom - navHeight - 12});
}

$('feature-links').addEventListener('click', event => {
  const button = event.target.closest('[data-feature]');
  if (!button) return;
  state.feature = button.dataset.feature;
  if (state.overview) renderFeature();
  scrollToPatient();
});
