import { measuredTimeLabel } from './measured-time';

/**
 * 界面显示的是「本机时区里的几点」，所以按本地时间构造时间戳。
 * 写死 `+08:00` 的固定偏移在 CI（UTC runner）上会整体挪 8 小时。
 */
function atLocal(month: number, day: number, hour: number, minute = 0): string {
  return new Date(2026, month - 1, day, hour, minute).toISOString();
}

describe('measuredTimeLabel', () => {
  it('shows the clock when the patient gave a real time', () => {
    expect(measuredTimeLabel(atLocal(10, 29, 7, 10), 'exact')).toBe(
      '10-29 07:10',
    );
  });

  it('names the daypart instead of pretending to know the minute', () => {
    // 患者只说「早上」：时间是该时段的代表时刻 07:00，界面必须写「大概」。
    expect(measuredTimeLabel(atLocal(10, 29, 7), 'daypart')).toBe(
      '10-29 早上（大概）',
    );
    expect(measuredTimeLabel(atLocal(10, 29, 20), 'daypart')).toBe(
      '10-29 晚上（大概）',
    );
  });

  it('falls back to the clock for an hour it cannot name', () => {
    // 认不出的时段宁可显示时刻，也不要叫错时段。
    expect(measuredTimeLabel(atLocal(10, 29, 9), 'daypart')).toBe(
      '10-29 09:00',
    );
  });

  it('treats a missing precision as exact', () => {
    expect(measuredTimeLabel(atLocal(10, 29, 9), undefined)).toBe('10-29 09:00');
  });
});
