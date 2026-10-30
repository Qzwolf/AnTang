import {
  parseChatStreamEvent,
  parseHealthProfile,
  parseHealthProfileCardDecision,
  parseHealthProfileCards,
  parseHealthProfileChangeResult,
  parseMessageHistory,
  parseProactiveCareSettings,
  parseProactiveCareNotificationData,
  parseWearableDashboard,
} from './types';

const assistantId = '019b2222-2222-7222-8222-222222222222';

function historyWith(sources: unknown) {
  return {
    messages: [
      {
        id: assistantId,
        client_message_id: null,
        role: 'assistant',
        status: 'completed',
        content: '请看这个来源 [S1]。',
        sources,
        created_at: '2026-07-13T10:00:00.000Z',
        completed_at: '2026-07-13T10:00:01.000Z',
      },
    ],
    next_before: null,
  };
}

describe('message history sources', () => {
  test('parses validated source metadata', () => {
    const history = parseMessageHistory(
      historyWith([
        {
          source_id: 'S1',
          title: '低血糖资料',
          url: 'https://example.com/hypoglycemia',
        },
      ]),
    );

    expect(history.messages[0].sources).toEqual([
      {
        sourceId: 'S1',
        title: '低血糖资料',
        url: 'https://example.com/hypoglycemia',
      },
    ]);
  });

  test('requires sources instead of silently guessing old history', () => {
    expect(() => parseMessageHistory(historyWith(undefined))).toThrow(
      'message.sources 必须是数组',
    );
  });

  test('rejects duplicate source identifiers', () => {
    expect(() =>
      parseMessageHistory(
        historyWith([
          { source_id: 'S1', title: '一', url: 'https://one.example' },
          { source_id: 'S1', title: '二', url: 'https://two.example' },
        ]),
      ),
    ).toThrow('message.sources 包含重复的 S1');
  });
});

test('parses a saved wearable dashboard without mixing source metrics', () => {
  const dashboard = parseWearableDashboard({
    period_start: '2026-08-27T12:00:00Z',
    period_end: '2026-09-26T12:00:00Z',
    sources: [{
      source_package: 'com.huami.watch.hmwatchmanager',
      device: { manufacturer: 'Amazfit', model: 'Active 2', device_type: 2 },
      latest_observed_at: '2026-09-26T11:00:00Z',
      daily: [{ day: '2026-09-26', steps: 200, distance_meters: 123.5 }],
      heart_rate_trend: [{ observed_at: '2026-09-26T11:00:00Z', beats_per_minute: 81 }],
      sleep_sessions: [],
      totals: {
        heart_rate_samples: 1,
        heart_rate_minimum: 81,
        heart_rate_maximum: 81,
        heart_rate_average: 81,
        steps: 200,
        distance_meters: 123.5,
        oxygen_saturation_samples: 0,
        oxygen_saturation_minimum: null,
        oxygen_saturation_average: null,
        resting_heart_rate_samples: 0,
        resting_heart_rate_minimum: null,
        sleep_sessions: 0,
        sleep_minutes: 0,
      },
    }],
  });
  expect(dashboard.sources[0]).toMatchObject({
    sourcePackage: 'com.huami.watch.hmwatchmanager',
    daily: [{ day: '2026-09-26', steps: 200, distanceMeters: 123.5 }],
    heartRateTrend: [{ beatsPerMinute: 81 }],
  });
});

describe('health profile responses', () => {
  test('parses confirmation and clarification cards without inventing values', () => {
    expect(
      parseHealthProfileCards({
        cards: [
          {
            id: '019b3333-3333-7333-8333-333333333333',
            kind: 'confirmation',
            target_type: 'personal_profile',
            field_name: 'weight_kg',
            operation: 'set',
            question: '把体重更新为 62 kg 吗？',
            proposed_value: { value: 62 },
            options: [
              { id: 'accept', label: '是，确认写入' },
              { id: 'reject', label: '暂不写入' },
            ],
            allow_custom_input: true,
            custom_input_placeholder: '输入体重和单位',
            created_at: '2026-07-14T10:00:00Z',
          },
          {
            id: '019b4444-4444-7444-8444-444444444444',
            kind: 'clarification',
            target_type: 'health_fact',
            field_name: 'allergy',
            operation: 'add',
            question: '你说的是药物过敏吗？',
            proposed_value: null,
            options: [{ id: 'reject', label: '暂不写入' }],
            allow_custom_input: true,
            custom_input_placeholder: null,
            created_at: '2026-07-14T10:01:00Z',
          },
        ],
      }).cards,
    ).toEqual([
      {
        id: '019b3333-3333-7333-8333-333333333333',
        kind: 'confirmation',
        targetType: 'personal_profile',
        fieldName: 'weight_kg',
        operation: 'set',
        question: '把体重更新为 62 kg 吗？',
        proposedValue: { value: 62 },
        options: [
          { id: 'accept', label: '是，确认写入' },
          { id: 'reject', label: '暂不写入' },
        ],
        allowCustomInput: true,
        customInputPlaceholder: '输入体重和单位',
        createdAt: '2026-07-14T10:00:00Z',
      },
      {
        id: '019b4444-4444-7444-8444-444444444444',
        kind: 'clarification',
        targetType: 'health_fact',
        fieldName: 'allergy',
        operation: 'add',
        question: '你说的是药物过敏吗？',
        proposedValue: null,
        options: [{ id: 'reject', label: '暂不写入' }],
        allowCustomInput: true,
        customInputPlaceholder: null,
        createdAt: '2026-07-14T10:01:00Z',
      },
    ]);
  });

  test('parses health profile decisions and stable activity phases', () => {
    expect(
      parseHealthProfileCardDecision({
        card_id: '019b3333-3333-7333-8333-333333333333',
        status: 'applied',
      }),
    ).toEqual({
      cardId: '019b3333-3333-7333-8333-333333333333',
      status: 'applied',
    });
    expect(
      parseChatStreamEvent({ type: 'agent_activity', phase: 'reading' }),
    ).toEqual({ type: 'agent_activity', phase: 'reading' });
  });

  test('parses an applied manual health profile change', () => {
    expect(
      parseHealthProfileChangeResult({
        change_id: '019b5555-5555-7555-8555-555555555555',
        status: 'applied',
        target_type: 'health_fact',
        field_name: 'allergy',
        operation: 'add',
        target_id: '019b6666-6666-7666-8666-666666666666',
        result_revision: 1,
      }),
    ).toEqual({
      changeId: '019b5555-5555-7555-8555-555555555555',
      status: 'applied',
      targetType: 'health_fact',
      fieldName: 'allergy',
      operation: 'add',
      targetId: '019b6666-6666-7666-8666-666666666666',
      resultRevision: 1,
    });
  });
});

describe('health profile overview response', () => {
  const response = {
    personal_profile: {
      sex: '女',
      age_years: 28,
      age_as_of_date: '2026-07-01',
      height_cm: '165.20',
      weight_kg: '58.30',
      resident_area: '上海',
      schedule_type: '规律作息',
      occupation: '设计师',
      revision: 3,
      updated_at: '2026-07-26T12:00:00+00:00',
    },
    health_facts: [
      {
        id: '019b7777-7777-7777-8777-777777777777',
        fact_type: 'treatment',
        statement: '目前使用胰岛素泵',
        assertion: 'present',
        temporal_status: 'current',
        effective_start: '2025-09-01',
        effective_end: null,
        revision: 2,
        created_at: '2026-07-20T10:00:00+08:00',
        updated_at: '2026-07-25T10:00:00+08:00',
      },
    ],
    heart_rate_trend: [
      {
        observed_at: '2026-07-26T18:00:00+08:00',
        beats_per_minute: 72,
      },
      {
        observed_at: '2026-07-26T21:59:00+08:00',
        beats_per_minute: 78,
      },
    ],
    wearable_latest: [
      {
        record_type: 'steps',
        observed_at: '2026-07-26T21:50:00+08:00',
        data: { count: 842 },
        source_package: 'com.huami.watch.hmwatchmanager',
      },
      {
        record_type: 'exercise',
        observed_at: '2026-07-26T19:30:00+08:00',
        data: { exercise_type: 79, title: '晚间散步' },
        source_package: 'com.huami.watch.hmwatchmanager',
      },
      {
        record_type: 'distance',
        observed_at: '2026-07-26T21:50:00+08:00',
        data: { meters: '628.40' },
        source_package: 'com.huami.watch.hmwatchmanager',
      },
      {
        record_type: 'elevation_gained',
        observed_at: '2026-07-26T21:50:00+08:00',
        data: { meters: '12.50' },
        source_package: 'com.huami.watch.hmwatchmanager',
      },
      {
        record_type: 'weight',
        observed_at: '2026-07-25T08:00:00+08:00',
        data: { kilograms: '58.10' },
        source_package: 'com.huami.watch.hmwatchmanager',
      },
      {
        record_type: 'respiratory_rate',
        observed_at: '2026-07-26T07:00:00+08:00',
        data: { breaths_per_minute: '15.50' },
        source_package: 'com.huami.watch.hmwatchmanager',
      },
      {
        record_type: 'resting_heart_rate',
        observed_at: '2026-07-26T07:00:00+08:00',
        data: { beats_per_minute: 63 },
        source_package: 'com.huami.watch.hmwatchmanager',
      },
      {
        record_type: 'heart_rate',
        observed_at: '2026-07-26T22:00:00+08:00',
        data: {
          samples: [
            {
              time: '2026-07-26T21:58:00+08:00',
              beats_per_minute: 76,
            },
            {
              time: '2026-07-26T21:59:00+08:00',
              beats_per_minute: 78,
            },
          ],
        },
        source_package: 'com.huami.watch.hmwatchmanager',
      },
      {
        record_type: 'sleep',
        observed_at: '2026-07-26T07:00:00+08:00',
        data: {
          stages: [
            {
              start_time: '2026-07-25T23:00:00+08:00',
              end_time: '2026-07-26T02:00:00+08:00',
              stage: 'deep',
            },
            {
              start_time: '2026-07-26T02:00:00+08:00',
              end_time: '2026-07-26T07:00:00+08:00',
              stage: 'light',
            },
          ],
        },
        source_package: 'com.huami.watch.hmwatchmanager',
      },
      {
        record_type: 'oxygen_saturation',
        observed_at: '2026-07-26T07:00:00+08:00',
        data: { percentage: '97.50' },
        source_package: 'com.huami.watch.hmwatchmanager',
      },
    ],
  };

  test('strictly parses the profile, facts and every wearable data shape', () => {
    const result = parseHealthProfile(response);

    expect(result.personalProfile).toMatchObject({
      ageYears: 28,
      heightCm: 165.2,
      weightKg: 58.3,
      residentArea: '上海',
    });
    expect(result.healthFacts).toEqual([
      expect.objectContaining({
        factType: 'treatment',
        statement: '目前使用胰岛素泵',
        temporalStatus: 'current',
      }),
    ]);
    expect(result.heartRateTrend).toEqual([
      { observedAt: '2026-07-26T18:00:00+08:00', beatsPerMinute: 72 },
      { observedAt: '2026-07-26T21:59:00+08:00', beatsPerMinute: 78 },
    ]);
    expect(result.wearableLatest).toHaveLength(10);
    expect(result.wearableLatest).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          recordType: 'heart_rate',
          data: {
            samples: [
              {
                time: '2026-07-26T21:58:00+08:00',
                beatsPerMinute: 76,
              },
              {
                time: '2026-07-26T21:59:00+08:00',
                beatsPerMinute: 78,
              },
            ],
          },
        }),
        expect.objectContaining({
          recordType: 'sleep',
          data: {
            stages: [
              {
                startTime: '2026-07-25T23:00:00+08:00',
                endTime: '2026-07-26T02:00:00+08:00',
                stage: 'deep',
              },
              {
                startTime: '2026-07-26T02:00:00+08:00',
                endTime: '2026-07-26T07:00:00+08:00',
                stage: 'light',
              },
            ],
          },
        }),
        expect.objectContaining({
          recordType: 'oxygen_saturation',
          data: { percentage: 97.5 },
        }),
      ]),
    );
  });

  test('rejects malformed decimals, duplicate record types and impossible dates', () => {
    expect(() =>
      parseHealthProfile({
        ...response,
        personal_profile: { ...response.personal_profile, weight_kg: 58.3 },
      }),
    ).toThrow('weight_kg 必须是十进制数字字符串');

    expect(() =>
      parseHealthProfile({
        ...response,
        wearable_latest: [
          response.wearable_latest[0],
          response.wearable_latest[0],
        ],
      }),
    ).toThrow('包含重复的 steps');

    expect(() =>
      parseHealthProfile({
        ...response,
        health_facts: [
          { ...response.health_facts[0], effective_start: '2026-02-30' },
        ],
      }),
    ).toThrow('必须是 YYYY-MM-DD 日期');
  });
});

describe('proactive care notification data', () => {
  test('accepts only a known care kind with valid message and delivery ids', () => {
    expect(
      parseProactiveCareNotificationData({
        kind: 'routine_check_in',
        message_id: '019b5555-5555-7555-8555-555555555555',
        delivery_id: '019b6666-6666-7666-8666-666666666666',
      }),
    ).toEqual({
      kind: 'routine_check_in',
      messageId: '019b5555-5555-7555-8555-555555555555',
      deliveryId: '019b6666-6666-7666-8666-666666666666',
    });

    expect(() =>
      parseProactiveCareNotificationData({
        kind: 'unknown',
        message_id: 'not-a-uuid',
        delivery_id: '019b6666-6666-7666-8666-666666666666',
      }),
    ).toThrow('notification.kind 无效');
  });
});

describe('proactive care settings', () => {
  const response = {
    routine_cadence: 'every_3_days',
    plan_follow_up_enabled: true,
    health_events_enabled: false,
    timezone: 'Asia/Shanghai',
    quiet_hours_start: '22:00',
    quiet_hours_end: '08:00',
    health_notification_preview_enabled: false,
  };

  test('parses the complete settings document without filling missing fields', () => {
    expect(parseProactiveCareSettings(response)).toEqual({
      routineCadence: 'every_3_days',
      planFollowUpEnabled: true,
      healthEventsEnabled: false,
      timezone: 'Asia/Shanghai',
      quietHoursStart: '22:00',
      quietHoursEnd: '08:00',
      healthNotificationPreviewEnabled: false,
      // 旧后端不带打扰预算字段：按「没算过、没暂停」处理，不报错。
      weeklyContactCount: 0,
      weeklyContactLimit: null,
      autoPaused: false,
      autoDowngradedAt: null,
    });
    expect(
      parseProactiveCareSettings({
        ...response,
        weekly_contact_count: 2,
        weekly_contact_limit: 4,
        auto_paused: true,
        auto_downgraded_at: '2026-10-20T01:00:00Z',
      }),
    ).toMatchObject({
      weeklyContactCount: 2,
      weeklyContactLimit: 4,
      autoPaused: true,
      autoDowngradedAt: '2026-10-20T01:00:00Z',
    });
    expect(() =>
      parseProactiveCareSettings({
        ...response,
        plan_follow_up_enabled: undefined,
      }),
    ).toThrow('care settings.plan_follow_up_enabled 必须是布尔值');
  });

  test.each([
    ['routine_cadence', 'sometimes', 'routine_cadence 无效'],
    ['quiet_hours_start', '9:00', 'quiet_hours_start 必须使用 HH:MM'],
    ['timezone', 'Not/AZone', 'timezone 必须是有效的 IANA 时区'],
  ])('rejects invalid %s', (field, value, message) => {
    expect(() =>
      parseProactiveCareSettings({ ...response, [field]: value }),
    ).toThrow(message);
  });
});
