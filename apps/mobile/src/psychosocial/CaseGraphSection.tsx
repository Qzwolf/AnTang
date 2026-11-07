import { useEffect, useRef, useState } from 'react';
import { ActivityIndicator, StyleSheet, Text, View } from 'react-native';

import type { ApiClient } from '../api/client';
import type { CaseGraph } from '../api/types';
import { colors, radii, spacing, typefaces } from '../ui/theme';

/**
 * 他自己说过的念头与情绪（只读）。
 *
 * 这些条目是对话里他自己讲出来的原话，系统只做归位与连线，不推断、不写诊断。
 * 所以这里没有编辑和删除：要改，得他在对话里再说一次。
 *
 * 视觉上刻意不做成「一张张带标签的小卡」：分类靠左侧那道色条，行里只留他的原话，
 * 一句很小的灰字写清它是念头还是身上心里的感觉，说明压到卡片底部。
 */

const THOUGHT_COLOR = '#8BA8A0';
const FEELING_COLOR = '#7FA7C9';

const LAYER_COLORS: Record<string, string> = {
  cognitive: THOUGHT_COLOR,
  affective: FEELING_COLOR,
};

function layerColor(layer: string): string {
  return LAYER_COLORS[layer] ?? THOUGHT_COLOR;
}

export type CaseGraphSectionProps = {
  api: Pick<ApiClient, 'getCaseGraph'>;
};

export function CaseGraphSection({ api }: CaseGraphSectionProps) {
  const [graph, setGraph] = useState<CaseGraph | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const mountedRef = useRef(true);

  useEffect(() => {
    mountedRef.current = true;
    let active = true;
    void api
      .getCaseGraph()
      .then((next) => {
        if (!active) return;
        setGraph(next);
        setLoadError(null);
      })
      .catch((error: unknown) => {
        if (!active) return;
        setLoadError(error instanceof Error ? error.message : '读取失败');
      })
      .finally(() => {
        if (!active) return;
        setLoading(false);
      });
    return () => {
      active = false;
      mountedRef.current = false;
    };
  }, [api]);

  const textOf = (id: string): string => {
    const node = graph?.nodes.find((item) => item.id === id);
    return node === undefined ? '' : node.text;
  };

  const hasNodes = (graph?.nodes.length ?? 0) > 0;

  return (
    <View style={styles.card}>
      <View style={styles.header}>
        <Text style={styles.title}>你和我说过的话</Text>
        <View
          accessibilityLabel="这些内容只能看，不能在这里改"
          style={styles.lock}
        >
          <View style={styles.lockShackle} />
          <View style={styles.lockBody} />
        </View>
      </View>
      {loading ? <ActivityIndicator color={colors.primary} /> : null}
      {loadError !== null ? <Text style={styles.error}>{loadError}</Text> : null}
      {!loading && loadError === null && !hasNodes ? (
        <Text style={styles.empty}>
          还没有记下什么。你讲过的念头和感受，会一条条记在这里。
        </Text>
      ) : null}
      {graph?.nodes.map((node) => (
        <View key={node.id} style={styles.row}>
          <View style={[styles.bar, { backgroundColor: layerColor(node.layer) }]} />
          <View style={styles.rowBody}>
            <Text style={styles.rowText}>{node.text}</Text>
            <Text style={styles.rowMeta}>
              {node.kindLabel}
              {node.intensity === null ? '' : ` · 强度 ${node.intensity}/5`}
            </Text>
          </View>
        </View>
      ))}
      {graph !== null && graph.edges.length > 0 ? (
        <View style={styles.edgeBlock}>
          <Text style={styles.edgeLabel}>他讲清的因果</Text>
          {graph.edges.map((edge) => (
            <Text key={`${edge.fromNodeId}-${edge.toNodeId}`} style={styles.edgeText}>
              {textOf(edge.fromNodeId)} → {edge.relationLabel} →{' '}
              {textOf(edge.toNodeId)}
            </Text>
          ))}
        </View>
      ) : null}
      {hasNodes ? (
        <Text style={styles.legend}>灰绿是念头，浅蓝是身上和心里的感觉</Text>
      ) : null}
      <Text style={styles.footnote}>想改，就在对话里再说一次</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  card: {
    backgroundColor: colors.paper,
    borderRadius: radii.md,
    gap: spacing.sm,
    padding: spacing.lg,
  },
  header: {
    alignItems: 'center',
    flexDirection: 'row',
    justifyContent: 'space-between',
  },
  title: {
    color: colors.ink,
    fontFamily: typefaces.serif,
    fontSize: 18,
  },
  lock: {
    alignItems: 'center',
    justifyContent: 'flex-end',
    paddingBottom: 2,
  },
  lockShackle: {
    borderBottomWidth: 0,
    borderColor: colors.muted,
    borderTopLeftRadius: 5,
    borderTopRightRadius: 5,
    borderWidth: 1.5,
    height: 7,
    width: 9,
  },
  lockBody: {
    borderColor: colors.muted,
    borderRadius: 2,
    borderWidth: 1.5,
    height: 9,
    marginTop: -1,
    width: 14,
  },
  empty: {
    color: colors.muted,
    fontFamily: typefaces.sans,
    fontSize: 14,
    lineHeight: 22,
  },
  error: {
    color: colors.danger,
    fontFamily: typefaces.sans,
    fontSize: 13,
  },
  row: {
    flexDirection: 'row',
    gap: spacing.md,
    paddingVertical: spacing.xs,
  },
  bar: {
    alignSelf: 'stretch',
    borderRadius: 2,
    marginVertical: 3,
    width: 3,
  },
  rowBody: {
    flex: 1,
    gap: 3,
  },
  rowText: {
    color: colors.ink,
    fontFamily: typefaces.sans,
    fontSize: 16,
    lineHeight: 23,
  },
  rowMeta: {
    color: colors.muted,
    fontFamily: typefaces.sans,
    fontSize: 12,
  },
  edgeBlock: {
    borderTopColor: colors.paperMuted,
    borderTopWidth: 1,
    gap: spacing.xs,
    marginTop: spacing.xs,
    paddingTop: spacing.sm,
  },
  edgeLabel: {
    color: colors.muted,
    fontFamily: typefaces.sans,
    fontSize: 12,
  },
  edgeText: {
    color: colors.ink,
    fontFamily: typefaces.sans,
    fontSize: 14,
    lineHeight: 21,
  },
  legend: {
    color: colors.muted,
    fontFamily: typefaces.sans,
    fontSize: 12,
    lineHeight: 18,
  },
  footnote: {
    color: colors.muted,
    fontFamily: typefaces.sans,
    fontSize: 12,
  },
});
