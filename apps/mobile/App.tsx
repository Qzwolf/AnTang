import { useCallback, useEffect, useRef, useState } from "react";
import {
  ActivityIndicator,
  Alert,
  Modal,
  Pressable,
  StyleSheet,
  Text,
  View,
} from "react-native";
import { SafeAreaProvider, SafeAreaView } from "react-native-safe-area-context";

import { appClient, setSessionExpiredListener } from "./src/api/app-client";
import { errorMessage, SessionExpiredError } from "./src/api/client";
import type { User } from "./src/api/types";
import { AuthScreen } from "./src/auth/AuthScreen";
import { AdministrationScreen } from "./src/administration/AdministrationScreen";
import { ClinicianApplicationScreen } from "./src/administration/ClinicianApplicationScreen";
import { secureTokenStore } from "./src/auth/token-store";
import { ChatScreen, type ChatPage } from "./src/chat/ChatScreen";
import { useGadgetbridge } from "./src/health-connect/useGadgetbridge";
import { useHealthConnect } from "./src/health-connect/use-health-connect";
import { LiveHeartRateProvider } from "./src/live-heart-rate/LiveHeartRateProvider";
import { AppTabBar, type AppTabKey } from "./src/navigation/tab-bar";
import { MindScreen } from "./src/navigation/mind-screen";
import { ProfileScreen } from "./src/navigation/profile-screen";
import { RecordScreen } from "./src/navigation/record-screen";
import { ClinicianScreen } from "./src/patient-access/patient-access-screen";
import { DeleteAccountScreen } from "./src/settings/delete-account-screen";
import { exportAndShare } from "./src/settings/export-data";
import { installedVersion } from "./src/update/release";
import { BrandMark } from "./src/ui/brand-mark";
import { colors, radii, spacing, typefaces } from "./src/ui/theme";

type AppState =
  | { kind: "loading" }
  | { kind: "signed-out"; message?: string }
  | { kind: "signed-in"; user: User }
  | { kind: "startup-error"; message: string };

export default function App() {
  return (
    <SafeAreaProvider>
      <AppRoot />
    </SafeAreaProvider>
  );
}

function AppRoot() {
  const [state, setState] = useState<AppState>({ kind: "loading" });
  const [startupAttempt, setStartupAttempt] = useState(0);
  const api = appClient;
  const updateUser = useCallback(
    (user: User) => setState({ kind: "signed-in", user }),
    [],
  );

  useEffect(
    () =>
      setSessionExpiredListener(() => {
        setState({ kind: "signed-out", message: "登录已过期，请重新登录。" });
      }),
    [],
  );

  useEffect(() => {
    let active = true;

    async function restoreSession() {
      try {
        const tokens = await secureTokenStore.load();

        if (tokens === null) {
          if (active) setState({ kind: "signed-out" });
          return;
        }

        const user = await api.me();
        if (active) setState({ kind: "signed-in", user });
      } catch (error) {
        if (active) {
          if (error instanceof SessionExpiredError) {
            setState({ kind: "signed-out", message: error.message });
          } else {
            setState({ kind: "startup-error", message: errorMessage(error) });
          }
        }
      }
    }

    void restoreSession();

    return () => {
      active = false;
    };
  }, [api, startupAttempt]);

  if (state.kind === "loading") {
    return (
      <SafeAreaView style={styles.centered}>
        <BrandMark size={62} />
        <Text style={styles.loadingTitle}>安糖心语</Text>
        <View style={styles.loadingRow}>
          <ActivityIndicator color={colors.primary} size="small" />
          <Text style={styles.loadingText}>正在加载</Text>
        </View>
      </SafeAreaView>
    );
  }

  if (state.kind === "startup-error") {
    return (
      <SafeAreaView style={styles.centered}>
        <View style={styles.errorCard}>
          <View style={styles.errorMark}>
            <Text style={styles.errorMarkText}>!</Text>
          </View>
          <Text style={styles.errorTitle}>暂时没有连接上</Text>
          <Text selectable style={styles.errorText}>
            {state.message}
          </Text>
          <Pressable
            accessibilityRole="button"
            onPress={() => {
              setState({ kind: "loading" });
              setStartupAttempt((attempt) => attempt + 1);
            }}
            style={({ pressed }) => [
              styles.actionButton,
              pressed && styles.actionButtonPressed,
            ]}
          >
            <Text style={styles.actionButtonText}>再试一次</Text>
          </Pressable>
        </View>
      </SafeAreaView>
    );
  }

  if (state.kind === "signed-out") {
    return (
      <AuthScreen
        api={api}
        initialMessage={state.message}
        onAuthenticated={(user) => setState({ kind: "signed-in", user })}
      />
    );
  }

  if (state.user.role === "researcher") {
    return (
      <AdministrationScreen
        api={api}
        user={state.user}
        onSignedOut={() => setState({ kind: "signed-out" })}
      />
    );
  }

  if (
    state.user.role === "patient" &&
    ["pending", "rejected"].includes(state.user.applicationStatus ?? "")
  ) {
    return (
      <ClinicianApplicationScreen
        api={api}
        onUserUpdated={updateUser}
        onSignedOut={() => setState({ kind: "signed-out" })}
      />
    );
  }

  if (state.user.role === "clinician") {
    return (
      <ClinicianScreen
        api={api}
        user={state.user}
        onSignedOut={() => setState({ kind: "signed-out" })}
      />
    );
  }

  return (
    <PatientShell
      api={api}
      onSignedOut={() => setState({ kind: "signed-out" })}
      user={state.user}
    />
  );
}

/**
 * 患者端外壳：底部四个 tab（对话 / 记录 / 心理 / 我的），抽屉只放会话列表。
 * 四个页面都保持挂载，切 tab 只是隐藏，这样正在进行的对话和实时心率不会被打断。
 */
function PatientShell({
  api,
  user,
  onSignedOut,
}: {
  api: typeof appClient;
  user: User;
  onSignedOut: () => void;
}) {
  const [tab, setTab] = useState<AppTabKey>("chat");
  const [pageRequest, setPageRequest] = useState<{
    page: ChatPage;
    id: number;
  } | null>(null);
  const [showDeleteAccount, setShowDeleteAccount] = useState(false);
  const [exporting, setExporting] = useState(false);
  const nextPageId = useRef(0);

  // 手环两条通道挂在 shell 上：聊天页要用它们（关怀设置、看板刷新、实时心率），
  // 记录页要用它们展示连接状态，一处创建避免两个实例各自轮询、互相覆盖状态。
  const healthConnect = useHealthConnect(user.id, api);
  const gadgetbridge = useGadgetbridge(api);

  const openPage = useCallback((page: ChatPage) => {
    nextPageId.current += 1;
    setPageRequest({ page, id: nextPageId.current });
    setTab("chat");
  }, []);

  const signOut = useCallback(async () => {
    try {
      await api.logout();
    } catch {
      // 断网时服务端会话删不掉，但本地 token 该清还是要清，否则这台手机再也登不出去。
    }
    onSignedOut();
  }, [api, onSignedOut]);

  const exportData = useCallback(async () => {
    if (exporting) return;
    setExporting(true);
    try {
      const result = await exportAndShare(api);
      Alert.alert("导出好了", `${result.fileName}\n${result.summary}`);
    } catch (error) {
      Alert.alert("没能导出", errorMessage(error));
    } finally {
      setExporting(false);
    }
  }, [api, exporting]);

  const installed = installedVersion();
  const versionLabel =
    installed.code > 0
      ? `当前版本 ${installed.name} · ${installed.code}`
      : `当前版本 ${installed.name}`;

  return (
    <View style={styles.shell}>
      {/* 实时心率会话挂在四个 tab 的壳上：健康看板、对话、记录页共用同一条 BLE 连接，
          切换页面不会断，只有用户点「停止监测」或退到后台才会断。 */}
      <LiveHeartRateProvider active>
      <View style={styles.shellBody}>
        <View
          pointerEvents={tab === "chat" ? "auto" : "none"}
          style={[styles.tabPage, tab === "chat" ? null : styles.tabPageHidden]}
        >
          <ChatScreen
            api={api}
            gadgetbridge={gadgetbridge}
            healthConnect={healthConnect}
            onOpenProfile={() => setTab("profile")}
            pageRequest={pageRequest}
            user={user}
          />
        </View>
        <View
          pointerEvents={tab === "records" ? "auto" : "none"}
          style={[styles.tabPage, tab === "records" ? null : styles.tabPageHidden]}
        >
          <RecordScreen
            api={api}
            gadgetbridge={gadgetbridge}
            healthConnect={healthConnect}
            onImport={() => openPage("importSensor")}
            onRecordGlucose={() => openPage("glucose")}
            visible={tab === "records"}
          />
        </View>
        <View
          pointerEvents={tab === "mind" ? "auto" : "none"}
          style={[styles.tabPage, tab === "mind" ? null : styles.tabPageHidden]}
        >
          <MindScreen
            api={api}
            onOpenCareSettings={() => openPage("care")}
            visible={tab === "mind"}
          />
        </View>
        <View
          pointerEvents={tab === "profile" ? "auto" : "none"}
          style={[styles.tabPage, tab === "profile" ? null : styles.tabPageHidden]}
        >
          <ProfileScreen
            createdAt={user.createdAt ?? null}
            name={user.fullName ?? user.username}
            onDeleteAccount={() => setShowDeleteAccount(true)}
            onExportData={() => void exportData()}
            onOpenCareSettings={() => openPage("settings")}
            onOpenHealthDashboard={() => openPage("dashboard")}
            onOpenHealthProfile={() => openPage("health")}
            onOpenPatientAccess={() => openPage("patientAccess")}
            onOpenSettings={() => openPage("settings")}
            onSignOut={() => void signOut()}
            username={user.username}
            versionLabel={versionLabel}
          />
        </View>
      </View>
      <AppTabBar active={tab} onChange={setTab} />
      </LiveHeartRateProvider>

      <Modal
        animationType="slide"
        onRequestClose={() => setShowDeleteAccount(false)}
        presentationStyle="fullScreen"
        visible={showDeleteAccount}
      >
        <DeleteAccountScreen
          api={api}
          onCancel={() => setShowDeleteAccount(false)}
          onDeleted={() => {
            setShowDeleteAccount(false);
            onSignedOut();
          }}
        />
      </Modal>
    </View>
  );
}

const styles = StyleSheet.create({
  shell: {
    flex: 1,
    backgroundColor: colors.background,
  },
  shellBody: {
    flex: 1,
  },
  tabPage: {
    ...StyleSheet.absoluteFill,
  },
  tabPageHidden: {
    opacity: 0,
  },
  centered: {
    flex: 1,
    alignItems: "center",
    justifyContent: "center",
    gap: spacing.sm,
    paddingHorizontal: spacing.xl,
    backgroundColor: colors.background,
  },
  loadingTitle: {
    color: colors.ink,
    fontFamily: typefaces.sansMedium,
    fontSize: 28,
    fontWeight: "700",
  },
  loadingRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: spacing.xs,
    paddingTop: spacing.sm,
  },
  loadingText: {
    color: colors.muted,
    fontFamily: typefaces.sans,
    fontSize: 13,
  },
  errorCard: {
    width: "100%",
    maxWidth: 420,
    alignItems: "center",
    gap: spacing.sm,
    padding: spacing.xl,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: colors.line,
    borderRadius: radii.xl,
    borderCurve: "continuous",
    backgroundColor: colors.paper,
  },
  errorMark: {
    width: 42,
    height: 42,
    alignItems: "center",
    justifyContent: "center",
    marginBottom: spacing.xs,
    borderRadius: radii.pill,
    backgroundColor: colors.coralSoft,
  },
  errorMarkText: {
    color: colors.danger,
    fontFamily: typefaces.serif,
    fontSize: 24,
    fontWeight: "700",
  },
  errorTitle: {
    color: colors.ink,
    fontFamily: typefaces.sansMedium,
    fontSize: 24,
    fontWeight: "700",
  },
  errorText: {
    color: colors.muted,
    fontFamily: typefaces.sans,
    fontSize: 14,
    lineHeight: 21,
    textAlign: "center",
  },
  actionButton: {
    marginTop: spacing.sm,
    paddingHorizontal: spacing.xl,
    paddingVertical: spacing.sm,
    borderRadius: radii.pill,
    backgroundColor: colors.primary,
  },
  actionButtonPressed: { opacity: 0.72 },
  actionButtonText: {
    color: colors.paper,
    fontFamily: typefaces.sansMedium,
    fontSize: 14,
  },
});
