const NS = 'http://www.w3.org/2000/svg';

export function glucoseValue(mgDl, unit = 'mmol') {
  return unit === 'mg' ? mgDl : mgDl / 18;
}

export function chartLayout(records, start, end, gapHours = 6) {
  const begin = new Date(start).getTime();
  const finish = new Date(end).getTime();
  if (!Number.isFinite(begin) || !Number.isFinite(finish) || finish <= begin) throw new Error('曲线时间范围不正确');
  const points = records.map(record => {
    const time = new Date(record.time).getTime();
    if (!Number.isFinite(time) || !Number.isFinite(record.value)) throw new Error('曲线记录格式不正确');
    return { ...record, time };
  }).sort((a, b) => a.time - b.time);
  const segments = [];
  for (const point of points) {
    const previous = segments.at(-1)?.at(-1);
    if (!previous || point.time - previous.time > gapHours * 3600000 || point.source !== previous.source) segments.push([point]);
    else segments.at(-1).push(point);
  }
  return { points, segments, begin, finish };
}

function svgNode(tag, attributes = {}, text) {
  const node = document.createElementNS(NS, tag);
  for (const [key, value] of Object.entries(attributes)) node.setAttribute(key, String(value));
  if (text !== undefined) node.textContent = String(text);
  return node;
}

function empty(container, text) {
  const node = document.createElement('div');
  node.className = 'chart-empty';
  node.textContent = text;
  container.replaceChildren(node);
}

export function lineChart(container, records, { start, end, unit = '', reference = null, compact = false, gapHours = 6, simulated = false, fullStart = start, fullEnd = end, onActivate }) {
  const requestedStart = new Date(start).getTime();
  const requestedEnd = new Date(end).getTime();
  const visibleRecords = records.filter(record => {
    const time = new Date(record.time).getTime();
    return time >= requestedStart && time <= requestedEnd;
  });
  const { points, segments } = chartLayout(visibleRecords, start, end, gapHours);
  if (!points.length) { empty(container, '此时间范围内暂无记录'); return; }
  const begin = requestedStart;
  const finish = requestedEnd;
  const width = 1000, height = compact ? 190 : 300;
  const left = 46, right = 18, top = 18, bottom = height - 36;
  const values = [...points.map(point => point.value), ...(reference ?? [])];
  const low = Math.max(0, Math.floor(Math.min(...values) * .8));
  const high = Math.max(low + 1, Math.ceil(Math.max(...values) * 1.16));
  const x = time => left + (time - begin) / (finish - begin) * (width - left - right);
  const y = value => bottom - (value - low) / (high - low) * (bottom - top);
  const svg = svgNode('svg', { viewBox: `0 0 ${width} ${height}`, role: 'group', 'aria-label': `${unit} ${simulated ? "模拟演示" : "已保存"}记录变化曲线` });
  svg.append(svgNode('title', {}, `共 ${points.length} 个${simulated ? "模拟演示" : "记录"}采样点；间隔超过 ${gapHours} 小时断开连线。`));
  if (reference) svg.append(svgNode('rect', { class: 'reference', x: left, y: y(reference[1]), width: width - left - right, height: y(reference[0]) - y(reference[1]) }));
  for (let i = 0; i <= 4; i++) {
    const value = low + (high - low) * i / 4;
    svg.append(svgNode('line', { class: 'grid', x1: left, x2: width - right, y1: y(value), y2: y(value) }));
    svg.append(svgNode('text', { x: left - 12, y: y(value) + 4, 'text-anchor': 'end' }, value >= 30 ? Math.round(value) : value.toFixed(1)));
  }
  for (let i = 0; i <= 5; i++) {
    const time = begin + (finish - begin) * i / 5;
    const date = new Date(time);
    svg.append(svgNode('text', { x: x(time), y: height - 10, 'text-anchor': i === 0 ? 'start' : i === 5 ? 'end' : 'middle' }, `${date.getMonth() + 1}/${date.getDate()} ${date.getHours()}:00`));
  }
  for (const segment of segments) {
    if (segment.length > 1) svg.append(svgNode('path', { class: 'line', d: segment.map((point, i) => `${i ? 'L' : 'M'} ${x(point.time).toFixed(2)} ${y(point.value).toFixed(2)}`).join(' ') }));
  }
  const detail = document.createElement('div');
  detail.className = 'chart-point-detail'; detail.setAttribute('role', 'status'); detail.setAttribute('aria-live', 'polite');
  detail.textContent = '点击采样点查看测量时间与具体数值，可放大附近时段。';
  const controls = document.createElement('div'); controls.className = 'chart-point-controls';
  const zoom = document.createElement('button'); zoom.type = 'button'; zoom.className = 'secondary'; zoom.textContent = '放大附近2小时'; zoom.disabled = true;
  const reset = document.createElement('button'); reset.type = 'button'; reset.className = 'secondary'; reset.textContent = '恢复完整范围'; reset.disabled = begin === new Date(fullStart).getTime() && finish === new Date(fullEnd).getTime();
  let selected = null, selectedCircle = null;
  const redraw = (nextStart, nextEnd) => lineChart(container, records, { start: nextStart, end: nextEnd, unit, reference, compact, gapHours, simulated, fullStart, fullEnd, onActivate });
  zoom.addEventListener('click', () => {
    if (!selected) return;
    redraw(new Date(Math.max(new Date(fullStart).getTime(), selected.time - 3600000)).toISOString(), new Date(Math.min(new Date(fullEnd).getTime(), selected.time + 3600000)).toISOString());
  });
  reset.addEventListener('click', () => redraw(fullStart, fullEnd));
  controls.append(zoom, reset);
  for (const point of points) {
    const circle = svgNode('circle', { class: 'point', cx: x(point.time), cy: y(point.value), r: compact ? 2 : 3, tabindex: '0' });
    const description = `${new Date(point.time).toLocaleString('zh-CN', {hour12:false})}，${point.value.toFixed(unit === 'mg/dL' ? 0 : 2)} ${unit}${point.label ? '，' + point.label : ''}`;
    circle.setAttribute('role','button'); circle.setAttribute('aria-label',description); circle.setAttribute('aria-pressed','false');
    const selectPoint = () => {
      if (selectedCircle) { selectedCircle.setAttribute('r', compact ? '2' : '3'); selectedCircle.setAttribute('aria-pressed','false'); }
      selected = point; selectedCircle = circle;
      circle.setAttribute('r','7'); circle.setAttribute('aria-pressed','true');
      detail.textContent = description; zoom.disabled = false;
      onActivate?.(point);
    };
    circle.addEventListener('click',selectPoint);
    circle.addEventListener('keydown',event=>{if(event.key==='Enter'||event.key===' '){event.preventDefault();selectPoint();}});
    circle.append(svgNode('title', {}, `${new Date(point.time).toLocaleString()}\n${point.value.toFixed(1)} ${unit}${point.label ? '\n' + point.label : ''}`));
    svg.append(circle);
  }
  container.replaceChildren(svg, detail, controls);
}

export function stepsChart(container, days, start, end) {
  if (!days.length) { empty(container, '此时间范围内暂无步数记录'); return; }
  const begin = new Date(start).getTime(), finish = new Date(end).getTime();
  const max = Math.max(1, ...days.map(day => day.steps));
  const svg = svgNode('svg', { viewBox: '0 0 1000 180', role: 'img', 'aria-label': '每日已同步步数' });
  const barWidth = Math.max(2, Math.min(45, 820 / Math.max(1, (finish - begin) / 86400000)));
  for (const [i, day] of days.entries()) {
    const time = new Date(day.day + 'T12:00:00').getTime();
    const x = 40 + (time - begin) / (finish - begin) * 920;
    const height = day.steps / max * 125;
    const bar = svgNode('rect', { x: x - barWidth / 2, y: 140 - height, width: barWidth, height: Math.max(1, height), fill: '#96c7ab', rx: 3 });
    bar.append(svgNode('title', {}, `${day.day} · ${day.steps.toLocaleString()} 步`));
    svg.append(bar);
    if (days.length <= 12 || i % Math.ceil(days.length / 8) === 0) svg.append(svgNode('text', { x, y: 165, 'text-anchor': 'middle' }, day.day.slice(5)));
  }
  container.replaceChildren(svg);
}

export function eventChart(container, events, start, end) {
  const begin = new Date(start).getTime();
  const finish = new Date(end).getTime();
  const lanes = [
    {kind: 'bolus', label: '追加胰岛素', unit: 'U', color: '#92509d'},
    {kind: 'basal', label: '基础输注', unit: 'U/h', color: '#22758f'},
    {kind: 'temp_basal', label: '临时基础输注', unit: 'U/h', color: '#a46417'},
    {kind: 'meal', label: '饮食碳水', unit: 'g', color: '#258267'},
  ];
  if (!events.some(event => lanes.some(lane => lane.kind === event.kind))) {
    empty(container, '当前时段暂无胰岛素或饮食记录');
    return;
  }

  const x = time => 46 + (time - begin) / (finish - begin) * 936;
  const timeLabel = time => new Date(time).toLocaleString('zh-CN', {hour12: false});
  const svg = svgNode('svg', {viewBox: '0 0 1000 250', role: 'group', 'aria-label': '胰岛素与饮食时间轴'});
  const detail = document.createElement('p');
  detail.className = 'chart-point-detail';
  detail.setAttribute('role', 'status');
  detail.textContent = '点选圆点或横条，查看对应记录的时间和数值。';
  let selected = null;

  lanes.forEach(({kind, label, unit, color}, index) => {
    const y = 48 + index * 48;
    svg.append(svgNode('text', {x: 46, y: y - 17, class: `study-lane-${kind}`}, `${label} · ${unit}`));
    svg.append(svgNode('line', {x1: 46, x2: 982, y1: y, y2: y, class: 'grid'}));
    for (const event of events.filter(record => record.kind === kind)) {
      const startTime = new Date(event.measured_at).getTime();
      const endTime = event.ended_at ? new Date(event.ended_at).getTime() : startTime;
      const left = x(Math.max(begin, startTime));
      const right = x(Math.min(finish, endTime));
      const marker = right > left
        ? svgNode('rect', {x: left, y: y - 5, width: right - left, height: 10, fill: color, rx: 2})
        : svgNode('circle', {cx: left, cy: y, r: 5, fill: color});
      const when = timeLabel(startTime) + (event.ended_at ? ' 至 ' + timeLabel(endTime) : '');
      const description = `${label} · ${event.value} ${event.unit} · ${when}`;
      marker.setAttribute('class', 'study-event');
      marker.setAttribute('role', 'button');
      marker.setAttribute('tabindex', '0');
      marker.setAttribute('aria-label', description);
      marker.setAttribute('aria-pressed', 'false');
      marker.append(svgNode('title', {}, description));
      const activate = () => {
        selected?.setAttribute('aria-pressed', 'false');
        marker.setAttribute('aria-pressed', 'true');
        selected = marker;
        detail.textContent = description;
      };
      marker.addEventListener('click', activate);
      marker.addEventListener('keydown', event => {
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault();
          activate();
        }
      });
      svg.append(marker);
    }
  });
  for (let index = 0; index <= 5; index++) {
    const time = begin + (finish - begin) * index / 5;
    const date = new Date(time);
    const hours = String(date.getHours()).padStart(2, '0');
    const minutes = String(date.getMinutes()).padStart(2, '0');
    svg.append(svgNode('text', {x: x(time), y: 245,
      'text-anchor': index === 0 ? 'start' : index === 5 ? 'end' : 'middle'},
      `${date.getMonth() + 1}/${date.getDate()} ${hours}:${minutes}`));
  }
  container.replaceChildren(svg, detail);
}
