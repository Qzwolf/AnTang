import { useState } from 'react';
import { Linking, Pressable, StyleSheet, Text, View } from 'react-native';

import type { ChatSource } from '../api/types';
import { MessageMarkup, type MessageTone } from './message-markup';

type Props = {
  content: string;
  sources: ChatSource[];
  onOpenError: (message: string) => void;
  tone?: MessageTone;
};

function openSource(source: ChatSource, onOpenError: Props['onOpenError']) {
  void Linking.openURL(source.url).catch((error: unknown) => {
    const detail = error instanceof Error ? error.message : '未知错误';
    onOpenError(`无法打开来源 ${source.sourceId}：${detail}`);
  });
}

function sourceDomain(url: string) {
  return new URL(url).hostname.toLowerCase().replace(/^www\./, '');
}

/**
 * 医学回答末尾的那句固定提示由提示词原样要求，这里把它拆出来渲染成灰色小字：
 * 正文负责解释，灰字负责划清「这是知识参考，不是处方」。
 * 只认句末原句，不做模糊匹配——猜错会把正常正文误染灰。
 */
const MEDICAL_NOTE =
  '以上是健康知识参考，不能替代医生的诊断和处方；用药调整请先联系你的医生。';
const MEDICAL_NOTE_ALT =
  '以上是健康知识参考，不能替代医生的诊断和处方。用药调整请先联系你的医生。';

export function splitMedicalNote(content: string): {
  body: string;
  note: string | null;
} {
  const trimmed = content.trimEnd();
  for (const note of [MEDICAL_NOTE, MEDICAL_NOTE_ALT]) {
    if (!trimmed.endsWith(note)) continue;
    return { body: trimmed.slice(0, -note.length).trimEnd(), note };
  }
  return { body: content, note: null };
}

/** 只把后端确认过、并且出现在 sources 中的严格 [S数字] 变成链接。 */
export function MessageSources({
  content,
  sources,
  onOpenError,
  tone = 'assistant',
}: Props) {
  const [sourcesExpanded, setSourcesExpanded] = useState(false);
  const sourcesById = new Map(sources.map((source) => [source.sourceId, source]));
  const userTone = tone === 'user';
  const { body, note } = splitMedicalNote(content);

  return (
    <>
      <MessageMarkup
        content={body}
        onOpenSource={(source) => openSource(source, onOpenError)}
        sourcesById={sourcesById}
        tone={tone}
      />

      {note === null ? null : (
        <Text style={[styles.medicalNote, userTone && styles.userMedicalNote]}>
          {note}
        </Text>
      )}

      {sources.length === 0 ? null : (
        <View
          style={[
            styles.sources,
            userTone ? styles.userSourcesBorder : styles.assistantSourcesBorder,
          ]}
        >
          <Pressable
            accessibilityHint={
              sourcesExpanded ? '双击收起来源列表' : '双击展开来源列表'
            }
            accessibilityLabel={`${sourcesExpanded ? '收起' : '展开'}参考来源，共 ${sources.length} 条`}
            accessibilityRole="button"
            accessibilityState={{ expanded: sourcesExpanded }}
            hitSlop={8}
            onPress={() => setSourcesExpanded((expanded) => !expanded)}
            style={({ pressed }) => [
              styles.sourcesToggle,
              pressed && styles.pressed,
            ]}
          >
            <View style={styles.sourcesHeading}>
              <Text
                style={[
                  styles.sourcesTitle,
                  userTone ? styles.userSourceTitle : styles.assistantSourceTitle,
                ]}
              >
                参考来源
              </Text>
              <View
                style={[
                  styles.sourceCount,
                  userTone ? styles.userSourceCount : styles.assistantSourceCount,
                ]}
              >
                <Text
                  style={[
                    styles.sourceCountText,
                    userTone
                      ? styles.userSourceSecondary
                      : styles.assistantSourceSecondary,
                  ]}
                >
                  {sources.length}
                </Text>
              </View>
            </View>
            <Text
              style={[
                styles.toggleText,
                userTone
                  ? styles.userSourceSecondary
                  : styles.assistantSourceSecondary,
              ]}
            >
              {sourcesExpanded ? '收起' : '展开'}
            </Text>
          </Pressable>

          {sourcesExpanded ? (
            <View style={styles.sourceList}>
              {sources.map((source) => {
                const domain = sourceDomain(source.url);
                return (
                  <Pressable
                    accessibilityHint={`将在浏览器中打开 ${domain}`}
                    accessibilityLabel={`打开来源 ${source.sourceId}：${source.title}`}
                    accessibilityRole="link"
                    key={source.sourceId}
                    onPress={() => openSource(source, onOpenError)}
                    style={({ pressed }) => [
                      styles.source,
                      userTone ? styles.userSource : styles.assistantSource,
                      pressed && styles.pressed,
                    ]}
                  >
                    <View
                      style={[
                        styles.sourceId,
                        userTone
                          ? styles.userSourceId
                          : styles.assistantSourceId,
                      ]}
                    >
                      <Text
                        style={[
                          styles.sourceIdText,
                          userTone
                            ? styles.userSourceSecondary
                            : styles.assistantSourceSecondary,
                        ]}
                      >
                        {source.sourceId}
                      </Text>
                    </View>
                    <View style={styles.sourceCopy}>
                      <Text
                        numberOfLines={2}
                        style={[
                          styles.sourceTitle,
                          userTone
                            ? styles.userSourceTitle
                            : styles.assistantSourceTitle,
                        ]}
                      >
                        {source.title}
                      </Text>
                      <Text
                        numberOfLines={1}
                        style={[
                          styles.sourceDomain,
                          userTone
                            ? styles.userSourceSecondary
                            : styles.assistantSourceSecondary,
                        ]}
                      >
                        {domain}
                      </Text>
                    </View>
                  </Pressable>
                );
              })}
            </View>
          ) : null}
        </View>
      )}
    </>
  );
}

const styles = StyleSheet.create({
  sources: {
    marginTop: 11,
    paddingTop: 8,
    borderTopWidth: StyleSheet.hairlineWidth,
    gap: 8,
  },
  assistantSourcesBorder: { borderTopColor: '#E1E7E4' },
  userSourcesBorder: { borderTopColor: 'rgba(255, 255, 255, 0.22)' },
  sourcesToggle: {
    minHeight: 32,
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    gap: 12,
  },
  sourcesHeading: { flexDirection: 'row', alignItems: 'center', gap: 6 },
  sourcesTitle: { fontSize: 12, fontWeight: '700', letterSpacing: 0.16 },
  sourceCount: {
    minWidth: 18,
    height: 18,
    alignItems: 'center',
    justifyContent: 'center',
    paddingHorizontal: 5,
    borderRadius: 9,
  },
  assistantSourceCount: { backgroundColor: '#EDF3F0' },
  userSourceCount: { backgroundColor: 'rgba(255, 255, 255, 0.14)' },
  sourceCountText: {
    fontSize: 10,
    fontWeight: '700',
    fontVariant: ['tabular-nums'],
  },
  toggleText: { fontSize: 12, fontWeight: '600' },
  sourceList: { gap: 7 },
  source: {
    minHeight: 54,
    flexDirection: 'row',
    alignItems: 'center',
    gap: 9,
    paddingHorizontal: 10,
    paddingVertical: 9,
    borderRadius: 12,
    borderCurve: 'continuous',
  },
  assistantSource: { backgroundColor: '#F3F7F5' },
  userSource: { backgroundColor: 'rgba(255, 255, 255, 0.11)' },
  sourceId: {
    minWidth: 30,
    height: 24,
    alignItems: 'center',
    justifyContent: 'center',
    paddingHorizontal: 6,
    borderRadius: 8,
    borderCurve: 'continuous',
  },
  assistantSourceId: { backgroundColor: '#E3EEE9' },
  userSourceId: { backgroundColor: 'rgba(255, 255, 255, 0.13)' },
  sourceIdText: {
    fontSize: 10,
    fontWeight: '800',
    fontVariant: ['tabular-nums'],
  },
  sourceCopy: { flex: 1, gap: 2 },
  sourceTitle: { fontSize: 13, fontWeight: '600', lineHeight: 18 },
  sourceDomain: { fontSize: 11, lineHeight: 15 },
  assistantSourceTitle: { color: '#1F3028' },
  userSourceTitle: { color: '#F8FCFA' },
  assistantSourceSecondary: { color: '#68766F' },
  userSourceSecondary: { color: '#CCE7DA' },
  medicalNote: {
    marginTop: 10,
    fontSize: 11,
    lineHeight: 16,
    color: '#91A19E',
  },
  userMedicalNote: { color: '#CCE7DA' },
  pressed: { opacity: 0.62 },
});
