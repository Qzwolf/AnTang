import { useCallback, useEffect, useRef, useState } from 'react';
import {
  ActivityIndicator,
  Pressable,
  StyleSheet,
  Text,
  View,
} from 'react-native';

import type { ApiClient } from '../api/client';
import type {
  InstrumentOption,
  PsychosocialInstrument,
  PsychosocialProfile,
} from '../api/types';
import { colors, radii, spacing, typefaces } from '../ui/theme';

/**
 * 沟通方式自评。
 *
 * 题目、选项与说明都由服务端下发（`GET /api/v1/health-profile/psychosocial-instrument`）：
 * 换量表（HFS-II 授权到位那天）只改后端，装过的包不用重装。
 *
 * 现在这份题库是我们自己写的，**不是标准化量表**：HFS-II 与它的短版是受版权保护的
 * 量表，题目不能自拟，否则分数与国际常模不可比、分档阈值也失去依据。
 *
 * 为什么要问：低血糖恐惧高的人和察觉能力受损的人需要完全不同的说话方式与联系
 * 频率；这一页决定的是「怎么跟这个人说话」，不是判断情绪。
 */

function maxOptionValue(options: InstrumentOption[]): number {
  return options.reduce((max, option) => Math.max(max, option.value), 0);
}

function percentOf(values: number[], maxValue: number): number {
  if (values.length === 0 || maxValue <= 0) return 0;
  const total = values.reduce((sum, value) => sum + value, 0);
  return Math.round((total / (values.length * maxValue)) * 100);
}

export type PsychosocialSectionProps = {
  api: Pick<
    ApiClient,
    | 'getPsychosocialAssessment'
    | 'getPsychosocialInstrument'
    | 'recordPsychosocialAssessment'
  >;
};

export function PsychosocialSection({ api }: PsychosocialSectionProps) {
  const [profile, setProfile] = useState<PsychosocialProfile | null>(null);
  const [instrument, setInstrument] = useState<PsychosocialInstrument | null>(
    null,
  );
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  // 默认收起：7 道题直接摊开会把「我的」页拉得很长，先给一句摘要和「开始填写」。
  const [open, setOpen] = useState(false);
  const [answers, setAnswers] = useState<Record<string, number>>({});
  const [goldScore, setGoldScore] = useState<number | null>(null);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const mountedRef = useRef(true);

  useEffect(() => {
    mountedRef.current = true;
    let active = true;
    void Promise.all([
      api.getPsychosocialInstrument(),
      api.getPsychosocialAssessment(),
    ]).then(
      ([nextInstrument, nextProfile]) => {
        if (!active) return;
        setInstrument(nextInstrument);
        setProfile(nextProfile);
        setLoadError(null);
        setLoading(false);
      },
      (reason: unknown) => {
        if (!active) return;
        setLoadError(errorMessage(reason));
        setLoading(false);
      },
    );
    return () => {
      active = false;
      mountedRef.current = false;
    };
  }, [api]);

  const questions = instrument?.questions ?? [];
  const options = instrument?.options ?? [];
  const goldOptions = instrument?.goldOptions ?? [];
  const maxValue = maxOptionValue(options);
  const answeredCount = questions.filter(
    (question) => answers[question.id] !== undefined,
  ).length;
  const complete = questions.length > 0 && answeredCount === questions.length && goldScore !== null;

  const selectAnswer = useCallback((questionId: string, value: number) => {
    setAnswers((current) => ({ ...current, [questionId]: value }));
    setSaveError(null);
  }, []);

  function startOver() {
    setAnswers({});
    setGoldScore(null);
    setSaveError(null);
    setOpen(true);
  }

  async function submit() {
    if (!complete || saving || instrument === null) return;
    const behavior = questions
      .filter((question) => question.kind === 'behavior')
      .map((question) => answers[question.id]);
    const worry = questions
      .filter((question) => question.kind === 'worry')
      .map((question) => answers[question.id]);
    setSaving(true);
    setSaveError(null);
    try {
      const saved = await api.recordPsychosocialAssessment({
        fearBehaviorPercent: percentOf(behavior, maxValue),
        fearWorryPercent: percentOf(worry, maxValue),
        goldScore,
        instrument: instrument.id,
        assessedAt: new Date().toISOString(),
        // 逐题选择一起交上去：服务端按当前题库算分，换量表不用重打包。
        answers: questions.map((question) => ({
          id: question.id,
          value: answers[question.id],
        })),
      });
      if (!mountedRef.current) return;
      setProfile(saved);
      setOpen(false);
      setAnswers({});
      setGoldScore(null);
    } catch (reason) {
      if (mountedRef.current) setSaveError(errorMessage(reason));
    } finally {
      if (mountedRef.current) setSaving(false);
    }
  }

  return (
    <View style={styles.card}>
      <Text style={styles.kicker}>沟通方式</Text>
      <Text style={styles.title}>{instrument?.title ?? '糖糖怎么跟你说话'}</Text>

      {loading ? (
        <View style={styles.loadingRow}>
          <ActivityIndicator color={colors.primary} size="small" />
          <Text style={styles.loadingText}>正在读取…</Text>
        </View>
      ) : loadError !== null ? (
        <Text style={styles.errorText}>{loadError}</Text>
      ) : open && instrument !== null ? (
        <View>
          <Text style={styles.description}>{instrument.instructions}</Text>
          {profile === null ? null : (
            <Text style={styles.metaText}>
              上次填写：{formatDate(profile.assessedAt)} · 糖糖现在每周主动联系你约{' '}
              {profile.weeklyContactLimit} 次
            </Text>
          )}
          {questions.map((question, index) => (
            <View key={question.id} style={styles.question}>
              <Text style={styles.questionText}>
                {index + 1}. {question.text}
              </Text>
              <View style={styles.optionRow}>
                {options.map((option) => {
                  const selected = answers[question.id] === option.value;
                  return (
                    <Pressable
                      accessibilityRole="radio"
                      accessibilityState={{ selected, checked: selected }}
                      key={option.value}
                      onPress={() => selectAnswer(question.id, option.value)}
                      style={[styles.option, selected && styles.optionSelected]}
                    >
                      <Text
                        style={[
                          styles.optionText,
                          selected && styles.optionTextSelected,
                        ]}
                      >
                        {option.label}
                      </Text>
                    </Pressable>
                  );
                })}
              </View>
            </View>
          ))}

          <View style={styles.question}>
            <Text style={styles.questionText}>
              {questions.length + 1}. {instrument.goldLabel}
            </Text>
            <Text style={styles.goldHint}>{instrument.goldHint}</Text>
            <View style={styles.optionRow}>
              {goldOptions.map((option) => {
                const selected = goldScore === option.value;
                return (
                  <Pressable
                    accessibilityRole="radio"
                    accessibilityState={{ selected, checked: selected }}
                    key={option.value}
                    onPress={() => {
                      setGoldScore(option.value);
                      setSaveError(null);
                    }}
                    style={[styles.goldOption, selected && styles.optionSelected]}
                  >
                    <Text
                      style={[
                        styles.optionText,
                        selected && styles.optionTextSelected,
                      ]}
                    >
                      {option.label}
                    </Text>
                  </Pressable>
                );
              })}
            </View>
          </View>

          {saveError !== null ? (
            <Text style={styles.errorText}>{saveError}</Text>
          ) : null}

          <Text style={styles.progressText}>
            {complete
              ? '已经填完了，可以保存。'
              : `还差 ${questions.length - answeredCount + (goldScore === null ? 1 : 0)} 题。`}
          </Text>

          <Pressable
            accessibilityRole="button"
            accessibilityState={{ disabled: !complete || saving }}
            disabled={!complete || saving}
            onPress={() => void submit()}
            style={[styles.primaryButton, (!complete || saving) && styles.buttonDisabled]}
          >
            {saving ? (
              <ActivityIndicator color={colors.white} size="small" />
            ) : (
              <Text style={styles.primaryButtonText}>保存</Text>
            )}
          </Pressable>
          <Pressable
            accessibilityRole="button"
            onPress={() => {
              setOpen(false);
              setSaveError(null);
            }}
            style={styles.secondaryButton}
          >
            <Text style={styles.secondaryButtonText}>以后再说</Text>
          </Pressable>
        </View>
      ) : (
        <View>
          {profile === null ? (
            <Text style={styles.description}>
              还没填过。答完这 {questions.length + 1}{' '}
              个问题，糖糖会按你的情况调整说话方式和主动联系的频率。
            </Text>
          ) : (
            <View>
              <Text style={styles.description}>
                糖糖现在每周主动联系你约 {profile.weeklyContactLimit} 次。
              </Text>
              <Text style={styles.metaText}>
                上次填写：{formatDate(profile.assessedAt)}
              </Text>
            </View>
          )}
          <Pressable
            accessibilityRole="button"
            onPress={startOver}
            style={styles.primaryButton}
          >
            <Text style={styles.primaryButtonText}>
              {profile === null ? '开始填写' : '重新填写'}
            </Text>
          </Pressable>
        </View>
      )}

      <Text style={styles.noteText}>
        {instrument?.disclaimer ??
          '这几道题是我们自己写的，不是标准化量表，只用来决定沟通方式，不作为诊断依据。'}
      </Text>
    </View>
  );
}

function formatDate(value: string): string {
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  return `${parsed.getFullYear()}-${String(parsed.getMonth() + 1).padStart(2, '0')}-${String(
    parsed.getDate(),
  ).padStart(2, '0')}`;
}

function errorMessage(reason: unknown): string {
  return reason instanceof Error && reason.message.length > 0
    ? reason.message
    : '读取失败，请稍后再试';
}

const styles = StyleSheet.create({
  card: {
    backgroundColor: colors.paper,
    borderColor: colors.line,
    borderRadius: radii.md,
    borderWidth: 1,
    marginBottom: spacing.md,
    padding: spacing.md,
  },
  kicker: {
    color: colors.muted,
    fontFamily: typefaces.sansMedium,
    fontSize: 12,
    letterSpacing: 1,
    marginBottom: spacing.xxs,
  },
  title: {
    color: colors.ink,
    fontFamily: typefaces.sansMedium,
    fontSize: 20,
    marginBottom: spacing.xs,
  },
  description: {
    color: colors.text,
    fontFamily: typefaces.sans,
    fontSize: 14,
    lineHeight: 21,
    marginBottom: spacing.sm,
  },
  loadingRow: {
    alignItems: 'center',
    flexDirection: 'row',
    gap: spacing.xs,
  },
  loadingText: {
    color: colors.muted,
    fontFamily: typefaces.sans,
    fontSize: 14,
  },
  errorText: {
    color: colors.danger,
    fontFamily: typefaces.sans,
    fontSize: 13,
    lineHeight: 20,
    marginBottom: spacing.xs,
  },
  question: {
    borderTopColor: colors.line,
    borderTopWidth: 1,
    paddingTop: spacing.sm,
    marginBottom: spacing.sm,
  },
  questionText: {
    color: colors.ink,
    fontFamily: typefaces.sans,
    fontSize: 14,
    lineHeight: 21,
    marginBottom: spacing.xs,
  },
  goldHint: {
    color: colors.muted,
    fontFamily: typefaces.sans,
    fontSize: 12,
    marginBottom: spacing.xs,
  },
  optionRow: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: spacing.xs,
  },
  option: {
    backgroundColor: colors.paperMuted,
    borderRadius: radii.sm,
    paddingHorizontal: spacing.sm,
    paddingVertical: spacing.xs,
  },
  goldOption: {
    alignItems: 'center',
    backgroundColor: colors.paperMuted,
    borderRadius: radii.sm,
    minWidth: 36,
    paddingHorizontal: spacing.xs,
    paddingVertical: spacing.xs,
  },
  optionSelected: {
    backgroundColor: colors.primarySoft,
    borderColor: colors.primary,
    borderWidth: 1,
  },
  optionText: {
    color: colors.text,
    fontFamily: typefaces.sans,
    fontSize: 13,
  },
  optionTextSelected: {
    color: colors.primaryPressed,
    fontFamily: typefaces.sansMedium,
  },
  progressText: {
    color: colors.muted,
    fontFamily: typefaces.sans,
    fontSize: 12,
    marginTop: spacing.xs,
  },
  primaryButton: {
    alignItems: 'center',
    backgroundColor: colors.primary,
    borderRadius: radii.sm,
    marginTop: spacing.sm,
    paddingVertical: spacing.sm,
  },
  buttonDisabled: {
    backgroundColor: colors.disabled,
  },
  primaryButtonText: {
    color: colors.white,
    fontFamily: typefaces.sansMedium,
    fontSize: 15,
  },
  secondaryButton: {
    alignItems: 'center',
    paddingVertical: spacing.sm,
  },
  secondaryButtonText: {
    color: colors.muted,
    fontFamily: typefaces.sans,
    fontSize: 14,
  },
  metaText: {
    color: colors.muted,
    fontFamily: typefaces.sans,
    fontSize: 12,
    marginBottom: spacing.xs,
  },
  noteText: {
    color: colors.faint,
    fontFamily: typefaces.sans,
    fontSize: 12,
    lineHeight: 18,
    marginTop: spacing.sm,
  },
});
