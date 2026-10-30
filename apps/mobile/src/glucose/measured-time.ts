/** 测量时间的精度。
 *
 * 患者只说得出「早上」「晚上」的时候，后端把时间记成那个时段的代表时刻
 * （早上 07:00、晚上 20:00），并标成 `daypart`。这是诚实的近似，不是读数：
 * 界面上必须写「早上（大概）」，不能让 07:00 冒充一个精确到分钟的测量。
 */
export type MeasuredPrecision = 'exact' | 'daypart';

const DAYPART_LABELS: Record<number, string> = {
  0: '半夜',
  2: '半夜',
  7: '早上',
  12: '中午',
  15: '下午',
  20: '晚上',
};

function formatDay(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return `${date.getMonth() + 1}-${date.getDate()}`;
}

function formatClock(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  const hour = `${date.getHours()}`.padStart(2, '0');
  const minute = `${date.getMinutes()}`.padStart(2, '0');
  return `${hour}:${minute}`;
}

/** 「10-29 早上（大概）」或「10-29 07:10」。 */
export function measuredTimeLabel(
  iso: string,
  precision: MeasuredPrecision | undefined,
): string {
  const day = formatDay(iso);
  if (precision !== 'daypart') return `${day} ${formatClock(iso)}`;
  const hour = new Date(iso).getHours();
  // 认不出的时段就不猜名字，退回时刻：宁可显示 06:00，也不要叫错时段。
  const part = DAYPART_LABELS[hour];
  return part === undefined ? `${day} ${formatClock(iso)}` : `${day} ${part}（大概）`;
}
