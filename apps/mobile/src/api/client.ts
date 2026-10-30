import { fetch as expoFetch, type FetchRequestInit } from "expo/fetch";
import { File } from "expo-file-system";
import * as LegacyFileSystem from "expo-file-system/legacy";

import type { TokenStore } from "../auth/token-store";
import { consumeChatStream } from "./ndjson";
import {
  parseAuthResult,
  parseAttachmentUpload,
  parseHealthProfile,
  parseHealthProfileCardDecision,
  parseHealthProfileChangeResult,
  parseHealthProfileCards,
  parseHealthConnectRecordIdsPage,
  parseWearableDashboard,
  parseMessageHistory,
  parseProactiveCareSettings,
  parseTokenPair,
  parseUser,
  parseWearableImportResponse,
  readErrorPayload,
  type ChatStreamEvent,
  type ChatAttachment,
  type HealthProfile,
  type HealthConnectRecordIdsPage,
  type WearableDashboard,
  type HealthProfileCardAnswer,
  type HealthProfileCardDecision,
  type HealthProfileCards,
  type HealthProfileChange,
  type HealthProfileChangeResult,
  type MessageHistory,
  type ProactiveCareSettings,
  type ProactiveCareSettingsInput,
  type TokenPair,
  type User,
  type WearableImportResponse,
  type WearableRecord,
  parsePatientGrants,
  parsePatientGrant,
  parsePatientPage,
  parseClinicianApplication,
  parseOwnApplication,
  parseApplicationsPage,
  parseUserOverview,
  type ApplicationStatus,
  type RegistrationOptions,
  type PsychosocialProfile,
  parsePsychosocialProfile,
  type PsychosocialAssessmentInput,
  type PsychosocialInstrument,
  parsePsychosocialInstrument,
  type FearSummary,
  parseFearSummary,
  type FearBehaviorLog,
  parseFearBehaviorLogs,
  type BeliefEventRecord,
  parseBeliefEvents,
  parseTranscription,
  type Conversation,
  parseConversation,
  parseConversations,
  type GlucoseReading,
  parseGlucoseReading,
  parseGlucoseReadings,
  parseMealLog,
  parseMealLogs,
  parseMedicationLog,
  parseMedicationLogs,
  type MealLog,
  type MedicationLog,
  type GlucoseSummary,
  type GlucoseTarget,
  type GlucoseReport,
  parseGlucoseSummary,
  parseGlucoseTarget,
  parseGlucoseReports,
  parseGlucoseReport,
  parseGlucoseReportPreview,
  type GlucoseReportPreview,
  type CgmImportPreview,
  type CgmImportResult,
  type CgmSeries,
  type CgmSourceSystem,
  parseCgmImportPreview,
  parseCgmImportResult,
  parseCgmSeries,
} from "./types";

export type FetchFunction = (
  input: string | URL,
  init?: FetchRequestInit,
) => ReturnType<typeof expoFetch>;

type FetchResponse = Awaited<ReturnType<typeof expoFetch>>;

/**
 * 发送消息时手机正在收到的实时心率广播中最近的一条读数。
 * 请求体里对应后端的下划线字段 beats_per_minute / measured_at / device_name。
 */
export type LiveHeartRateReading = {
  beatsPerMinute: number;
  /** ISO 8601 时间字符串。 */
  measuredAt: string;
  deviceName: string | null;
};

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly code?: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

export class SessionExpiredError extends Error {
  constructor(message = "登录已过期，请重新登录。") {
    super(message);
    this.name = "SessionExpiredError";
  }
}

export class SessionChangedError extends Error {
  constructor(message = "登录账号已发生变化，旧请求已停止。") {
    super(message);
    this.name = "SessionChangedError";
  }
}

/**
 * 手机系统服务（华为健康、Health Connect、HMS）抛出来的是英文原生异常，
 * 患者既看不懂也不知道下一步做什么。这里把已知的几类换成能行动的中文；
 * 原始错误仍保留在 message 之外（调用方可在日志里拿到原始异常对象）。
 */
export function friendlyNativeError(message: string): string | null {
  if (
    /Binding to service failed|MISSING_INSTANCEID_SERVICE|Fetching the token failed/i.test(
      message,
    )
  ) {
    return "这台手机暂时连不上健康服务。装了「华为运动健康」的话，先在系统里打开并登录一次再试；也可以先用侧边栏的「华为手环导入」直接选导出的文件。";
  }
  if (/(java|kotlin)\.[A-Za-z.]*(Exception|Error)|ExecutionException/i.test(message)) {
    return "手机的系统服务返回了错误，稍后再试一次。";
  }
  return null;
}

export function errorMessage(error: unknown): string {
  if (!(error instanceof Error)) return "发生了未知错误";
  return friendlyNativeError(error.message) ?? error.message;
}

async function responseJson(response: FetchResponse): Promise<unknown> {
  try {
    return await response.json();
  } catch {
    throw new Error(
      `服务器返回 HTTP ${response.status}，响应内容不是有效 JSON`,
    );
  }
}

// 上传本地文件（录音、导出的 CSV）。expo 的 fetch 认这个 { bytes, name, type } 形状，
// 手写成 Blob 反而在 RN 上拿不到内容。
const BASE64_ALPHABET =
  "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";

/** 把二进制转成 base64：写缓存文件要用，RN 没有 Buffer。 */
function bytesToBase64(bytes: Uint8Array): string {
  let out = "";
  for (let index = 0; index < bytes.length; index += 3) {
    const a = bytes[index]!;
    const b = bytes[index + 1];
    const c = bytes[index + 2];
    out += BASE64_ALPHABET[a >> 2];
    out += BASE64_ALPHABET[((a & 3) << 4) | ((b ?? 0) >> 4)];
    out += b === undefined ? "=" : BASE64_ALPHABET[((b & 15) << 2) | ((c ?? 0) >> 6)];
    out += c === undefined ? "=" : BASE64_ALPHABET[c & 63];
  }
  return out;
}

function extensionForMimeType(mimeType: string): string {
  if (mimeType === "image/png") return "png";
  if (mimeType === "application/pdf") return "pdf";
  return "jpg";
}

function uploadForm(file: {
  mimeType: string;
  name: string;
  uri: string;
}): FormData {
  const form = new FormData();
  const body = new File(file.uri);
  form.append(
    "file",
    {
      bytes: () => body.bytes(),
      name: file.name,
      type: file.mimeType,
    } as unknown as Blob,
  );
  return form;
}

export class ApiClient {
  private refreshPromise: Promise<TokenPair> | null = null;
  private refreshVersion = -1;
  private refreshToken: string | null = null;
  private sessionVersion = 0;
  private sessionController = new AbortController();
  private tokenMutationTail: Promise<void> = Promise.resolve();

  constructor(
    private readonly baseUrl: string,
    private readonly tokenStore: TokenStore,
    private readonly onSessionExpired: () => void,
    private readonly fetchImpl: FetchFunction = expoFetch,
  ) {}

  private url(path: string): string {
    return `${this.baseUrl}${path}`;
  }

  private jsonInit(
    init: FetchRequestInit = {},
    accessToken?: string,
  ): FetchRequestInit {
    const headers = new Headers(init.headers);
    if (!headers.has("Accept")) headers.set("Accept", "application/json");
    if (init.body !== undefined && !(init.body instanceof FormData)) {
      headers.set("Content-Type", "application/json");
    }
    if (accessToken !== undefined)
      headers.set("Authorization", `Bearer ${accessToken}`);
    return { ...init, headers };
  }

  private async apiError(response: FetchResponse): Promise<ApiError> {
    const payload = await responseJson(response);
    const details = readErrorPayload(payload);
    return new ApiError(
      details.message ?? `服务器返回 HTTP ${response.status}`,
      response.status,
      details.code,
    );
  }

  private async consumeChatResponse(
    response: FetchResponse,
    onEvent: (event: ChatStreamEvent) => void,
  ): Promise<void> {
    if (!response.ok) throw await this.apiError(response);

    const contentType = response.headers
      .get("content-type")
      ?.split(";")[0]
      .trim();
    if (contentType !== "application/x-ndjson") {
      throw new Error(
        `聊天接口返回了错误的 Content-Type：${contentType ?? "缺失"}`,
      );
    }
    if (response.body === null) throw new Error("聊天接口没有返回响应流");

    await consumeChatStream(response.body, onEvent);
  }

  private assertSessionVersion(version: number): void {
    if (version !== this.sessionVersion) throw new SessionChangedError();
  }

  getSessionSignal(): AbortSignal {
    return this.sessionController.signal;
  }

  private advanceSession(): number {
    this.sessionController.abort(new SessionChangedError());
    this.sessionController = new AbortController();
    return ++this.sessionVersion;
  }

  /** Serializes SecureStore writes so an older async request can never finish last. */
  private mutateTokens<T>(
    version: number,
    mutation: () => Promise<T>,
  ): Promise<T> {
    const result = this.tokenMutationTail.then(async () => {
      this.assertSessionVersion(version);
      return mutation();
    });
    this.tokenMutationTail = result.then(
      () => undefined,
      () => undefined,
    );
    return result;
  }

  private async expireSession(expectedVersion: number): Promise<never> {
    this.assertSessionVersion(expectedVersion);
    const expiredVersion = this.advanceSession();
    await this.mutateTokens(expiredVersion, () => this.tokenStore.clear());
    this.assertSessionVersion(expiredVersion);
    this.onSessionExpired();
    throw new SessionExpiredError();
  }

  private async performRefresh(
    refreshToken: string,
    version: number,
  ): Promise<TokenPair> {
    const response = await this.fetchImpl(
      this.url("/api/v1/auth/refresh"),
      this.jsonInit({
        method: "POST",
        body: JSON.stringify({ refresh_token: refreshToken }),
      }),
    );
    this.assertSessionVersion(version);
    if (!response.ok) throw await this.apiError(response);

    const tokens = parseTokenPair(await responseJson(response));
    await this.mutateTokens(version, async () => {
      const currentTokens = await this.tokenStore.load();
      this.assertSessionVersion(version);
      if (currentTokens?.refreshToken !== refreshToken) {
        throw new SessionChangedError();
      }
      await this.tokenStore.save(tokens);
    });
    return tokens;
  }

  private refresh(refreshToken: string, version: number): Promise<TokenPair> {
    if (
      this.refreshPromise !== null &&
      this.refreshVersion === version &&
      this.refreshToken === refreshToken
    ) {
      return this.refreshPromise;
    }

    const promise = this.performRefresh(refreshToken, version);
    this.refreshPromise = promise;
    this.refreshVersion = version;
    this.refreshToken = refreshToken;
    const clear = () => {
      if (this.refreshPromise !== promise) return;
      this.refreshPromise = null;
      this.refreshVersion = -1;
      this.refreshToken = null;
    };
    void promise.then(clear, clear);
    return promise;
  }

  private async authorizedFetch(
    path: string,
    init: FetchRequestInit = {},
  ): Promise<FetchResponse> {
    if (init.signal?.aborted) {
      throw init.signal.reason instanceof Error
        ? init.signal.reason
        : new SessionChangedError();
    }
    const version = this.sessionVersion;
    const originalTokens = await this.tokenStore.load();
    this.assertSessionVersion(version);
    if (originalTokens === null) return this.expireSession(version);

    let response = await this.fetchImpl(
      this.url(path),
      this.jsonInit(init, originalTokens.accessToken),
    );
    this.assertSessionVersion(version);
    if (response.status !== 401) return response;

    const latestTokens = await this.tokenStore.load();
    this.assertSessionVersion(version);
    if (latestTokens === null) return this.expireSession(version);

    if (latestTokens.accessToken !== originalTokens.accessToken) {
      response = await this.fetchImpl(
        this.url(path),
        this.jsonInit(init, latestTokens.accessToken),
      );
      this.assertSessionVersion(version);
      if (response.status !== 401) return response;
    }

    let refreshed: TokenPair;
    try {
      refreshed = await this.refresh(latestTokens.refreshToken, version);
    } catch (error) {
      if (error instanceof SessionChangedError) throw error;
      if (error instanceof ApiError && error.status === 401) {
        return this.expireSession(version);
      }
      throw error;
    }

    this.assertSessionVersion(version);
    response = await this.fetchImpl(
      this.url(path),
      this.jsonInit(init, refreshed.accessToken),
    );
    this.assertSessionVersion(version);
    if (response.status === 401) return this.expireSession(version);
    return response;
  }

  async login(username: string, password: string): Promise<User> {
    return this.authenticate("/api/v1/auth/login", username, password);
  }

  async register(
    username: string,
    password: string,
    options?: RegistrationOptions,
  ): Promise<User> {
    return this.authenticate(
      "/api/v1/auth/register",
      username,
      password,
      options === undefined
        ? {}
        : {
            consent_accepted: options.consentAccepted,
            full_name: options.fullName,
            registration_kind: options.registrationKind,
            organization: options.organization,
          },
    );
  }

  private async authenticate(
    path: string,
    username: string,
    password: string,
    extra: Record<string, unknown> = {},
  ): Promise<User> {
    const version = this.advanceSession();
    await this.mutateTokens(version, () => this.tokenStore.clear());
    const response = await this.fetchImpl(
      this.url(path),
      this.jsonInit({
        method: "POST",
        body: JSON.stringify({ username, password, ...extra }),
      }),
    );
    this.assertSessionVersion(version);
    if (!response.ok) throw await this.apiError(response);

    const result = parseAuthResult(await responseJson(response));
    await this.mutateTokens(version, () => this.tokenStore.save(result.tokens));
    return result.user;
  }

  async me(signal?: AbortSignal): Promise<User> {
    const response = await this.authorizedFetch("/api/v1/auth/me", { signal });
    if (!response.ok) throw await this.apiError(response);
    return parseUser(await responseJson(response));
  }

  async updateAccountName(fullName: string): Promise<User> {
    const response = await this.authorizedFetch("/api/v1/auth/me", {
      method: "PATCH",
      body: JSON.stringify({ full_name: fullName }),
    });
    if (!response.ok) throw await this.apiError(response);
    return parseUser(await responseJson(response));
  }

  async getOwnClinicianApplication(signal?: AbortSignal) {
    const response = await this.authorizedFetch(
      "/api/v1/clinician-applications/me",
      { signal },
    );
    if (!response.ok) throw await this.apiError(response);
    return parseOwnApplication(await responseJson(response));
  }

  async submitClinicianApplication(fullName: string, organization: string) {
    const response = await this.authorizedFetch(
      "/api/v1/clinician-applications",
      {
        method: "POST",
        body: JSON.stringify({ full_name: fullName, organization }),
      },
    );
    if (!response.ok) throw await this.apiError(response);
    return parseClinicianApplication(await responseJson(response));
  }

  async getAdminOverview(signal?: AbortSignal) {
    const response = await this.authorizedFetch("/api/v1/admin/overview", {
      signal,
    });
    if (!response.ok) throw await this.apiError(response);
    return parseUserOverview(await responseJson(response));
  }

  async getClinicianApplications(
    status: ApplicationStatus,
    after?: string,
    signal?: AbortSignal,
  ) {
    const response = await this.authorizedFetch(
      `/api/v1/admin/clinician-applications?status=${status}&limit=30${after ? `&after=${encodeURIComponent(after)}` : ""}`,
      { signal },
    );
    if (!response.ok) throw await this.apiError(response);
    return parseApplicationsPage(await responseJson(response));
  }

  async reviewClinicianApplication(
    id: string,
    decision: "approve" | "reject",
    reason?: string,
  ) {
    const response = await this.authorizedFetch(
      `/api/v1/admin/clinician-applications/${encodeURIComponent(id)}/review`,
      {
        method: "POST",
        body: JSON.stringify({ decision, reason }),
      },
    );
    if (!response.ok) throw await this.apiError(response);
    return parseClinicianApplication(await responseJson(response));
  }

  async getClinicians(after?: string, signal?: AbortSignal) {
    const response = await this.authorizedFetch(
      `/api/v1/admin/clinicians?limit=30${after ? `&after=${encodeURIComponent(after)}` : ""}`,
      { signal },
    );
    if (!response.ok) throw await this.apiError(response);
    return parsePatientPage(await responseJson(response));
  }

  async createClinician(username: string, password: string, fullName: string) {
    const response = await this.authorizedFetch("/api/v1/admin/clinicians", {
      method: "POST",
      body: JSON.stringify({ username, password, full_name: fullName }),
    });
    if (!response.ok) throw await this.apiError(response);
    return parseUser(await responseJson(response));
  }

  async assignClinician(username: string) {
    const response = await this.authorizedFetch(
      "/api/v1/admin/clinicians/assign",
      {
        method: "POST",
        body: JSON.stringify({ username }),
      },
    );
    if (!response.ok) throw await this.apiError(response);
    return parseUser(await responseJson(response));
  }

  async logout(): Promise<void> {
    const version = this.sessionVersion;
    const tokens = await this.tokenStore.load();
    this.assertSessionVersion(version);
    if (tokens === null) return this.expireSession(version);
    const logoutVersion = this.advanceSession();

    const response = await this.fetchImpl(
      this.url("/api/v1/auth/logout"),
      this.jsonInit({
        method: "POST",
        body: JSON.stringify({ refresh_token: tokens.refreshToken }),
      }),
    );
    this.assertSessionVersion(logoutVersion);
    if (response.status === 401) return this.expireSession(logoutVersion);
    if (!response.ok) throw await this.apiError(response);
    await this.mutateTokens(logoutVersion, () => this.tokenStore.clear());
  }

  async getConversations(): Promise<Conversation[]> {
    const response = await this.authorizedFetch('/api/v1/chat/conversations');
    if (!response.ok) throw await this.apiError(response);
    return parseConversations(await responseJson(response));
  }

  async startConversation(): Promise<Conversation> {
    const response = await this.authorizedFetch('/api/v1/chat/conversations', {
      method: 'POST',
    });
    if (!response.ok) throw await this.apiError(response);
    return parseConversation(await responseJson(response));
  }

  async getMessages(
    before: string | null = null,
    conversationId: string | null = null,
  ): Promise<MessageHistory> {
    const params = new URLSearchParams({ limit: '50' });
    if (before !== null) params.set('before', before);
    if (conversationId !== null) params.set('conversation_id', conversationId);
    const response = await this.authorizedFetch(
      `/api/v1/chat/messages?${params.toString()}`,
    );
    if (!response.ok) throw await this.apiError(response);
    return parseMessageHistory(await responseJson(response));
  }

  async getMessageWindow(messageId: string): Promise<MessageHistory> {
    const response = await this.authorizedFetch(
      `/api/v1/chat/messages/${encodeURIComponent(messageId)}/window`,
    );
    if (!response.ok) throw await this.apiError(response);
    return parseMessageHistory(await responseJson(response));
  }

  /**
   * 把已经发出去的图片/报告下到缓存，返回本地 file:// 地址。
   *
   * 不能只把带 Authorization 的 URL 交给 <Image>：RN 0.86 的 Image 在网络请求上
   * 不带 headers（实测打到后端是 401），所以这里自己带 token 取字节、写成文件。
   */
  async getChatAttachmentImageSource(attachmentId: string): Promise<{
    uri: string;
  }> {
    const { uri } = await this.saveChatAttachment(attachmentId);
    return { uri };
  }

  /**
   * 把一条聊天附件取到本地缓存，返回文件路径和类型。
   *
   * 图片和 PDF 都走这一条：附件要带 token 才能下载，所以只能自己取字节写文件，
   * 不能把需要鉴权的 URL 直接交给 <Image> 或系统打开方式。
   */
  async saveChatAttachment(attachmentId: string): Promise<{
    uri: string;
    mimeType: string;
  }> {
    const version = this.sessionVersion;
    const tokens = await this.tokenStore.load();
    this.assertSessionVersion(version);
    if (tokens === null) return this.expireSession(version);

    const path = `/api/v1/chat/attachments/${encodeURIComponent(attachmentId)}/content`;
    const response = await expoFetch(this.url(path), {
      headers: { Authorization: `Bearer ${tokens.accessToken}` },
    });
    this.assertSessionVersion(version);
    if (!response.ok) {
      throw new Error(`附件下载失败：HTTP ${response.status}`);
    }

    const mimeType = response.headers.get("content-type") ?? "image/jpeg";
    const bytes = new Uint8Array(await response.arrayBuffer());
    const target = `${LegacyFileSystem.cacheDirectory ?? ""}chat-attachment-${attachmentId}.${extensionForMimeType(mimeType)}`;
    await LegacyFileSystem.writeAsStringAsync(target, bytesToBase64(bytes), {
      encoding: LegacyFileSystem.EncodingType.Base64,
    });
    return { uri: target, mimeType };
  }

  async syncPushInstallation(
    installationId: string,
    expoPushToken: string | null,
    permission: "undetermined" | "denied" | "granted",
    appVersion: string,
  ): Promise<void> {
    const response = await this.authorizedFetch(
      `/api/v1/proactive-care/installations/${encodeURIComponent(installationId)}`,
      {
        method: "PUT",
        body: JSON.stringify({
          expo_push_token: expoPushToken,
          permission,
          platform: "android",
          app_version: appVersion,
        }),
      },
    );
    if (!response.ok) throw await this.apiError(response);
  }

  async disablePushInstallation(installationId: string): Promise<void> {
    const response = await this.authorizedFetch(
      `/api/v1/proactive-care/installations/${encodeURIComponent(installationId)}`,
      { method: "DELETE" },
    );
    if (!response.ok) throw await this.apiError(response);
  }

  async markPushDeliveryOpened(deliveryId: string): Promise<void> {
    const response = await this.authorizedFetch(
      `/api/v1/proactive-care/deliveries/${encodeURIComponent(deliveryId)}/opened`,
      { method: "POST" },
    );
    if (!response.ok) throw await this.apiError(response);
  }

  async getProactiveCareSettings(): Promise<ProactiveCareSettings> {
    const response = await this.authorizedFetch(
      "/api/v1/proactive-care/settings",
    );
    if (!response.ok) throw await this.apiError(response);
    return parseProactiveCareSettings(await responseJson(response));
  }

  async putProactiveCareSettings(
    settings: ProactiveCareSettingsInput,
  ): Promise<ProactiveCareSettings> {
    const response = await this.authorizedFetch(
      "/api/v1/proactive-care/settings",
      {
        method: "PUT",
        body: JSON.stringify({
          routine_cadence: settings.routineCadence,
          plan_follow_up_enabled: settings.planFollowUpEnabled,
          health_events_enabled: settings.healthEventsEnabled,
          timezone: settings.timezone,
          quiet_hours_start: settings.quietHoursStart,
          quiet_hours_end: settings.quietHoursEnd,
          health_notification_preview_enabled:
            settings.healthNotificationPreviewEnabled,
        }),
      },
    );
    if (!response.ok) throw await this.apiError(response);
    return parseProactiveCareSettings(await responseJson(response));
  }

  async getHealthProfileCards(): Promise<HealthProfileCards> {
    const response = await this.authorizedFetch(
      "/api/v1/health-profile/cards?limit=50",
    );
    if (!response.ok) throw await this.apiError(response);
    return parseHealthProfileCards(await responseJson(response));
  }

  async getHealthProfile(signal?: AbortSignal): Promise<HealthProfile> {
    const response = await this.authorizedFetch("/api/v1/health-profile", {
      signal,
    });
    if (!response.ok) throw await this.apiError(response);
    return parseHealthProfile(await responseJson(response));
  }

  async getPatientGrants(signal?: AbortSignal) {
    const response = await this.authorizedFetch(
      "/api/v1/patient-access-grants",
      { signal },
    );
    if (!response.ok) throw await this.apiError(response);
    return parsePatientGrants(await responseJson(response));
  }

  async resolveClinician(username: string) {
    const response = await this.authorizedFetch(
      "/api/v1/patient-access-grants/resolve",
      {
        method: "POST",
        body: JSON.stringify({ username }),
      },
    );
    if (!response.ok) throw await this.apiError(response);
    return parseUser(await responseJson(response));
  }

  async grantPatientAccess(clinicianId: string) {
    const response = await this.authorizedFetch(
      "/api/v1/patient-access-grants",
      {
        method: "POST",
        body: JSON.stringify({ clinician_id: clinicianId }),
      },
    );
    if (!response.ok) throw await this.apiError(response);
    return parsePatientGrant(await responseJson(response));
  }

  async revokePatientAccess(grantId: string) {
    const response = await this.authorizedFetch(
      `/api/v1/patient-access-grants/${grantId}`,
      { method: "DELETE" },
    );
    if (!response.ok) throw await this.apiError(response);
  }

  async getPatients(after?: string, signal?: AbortSignal) {
    const response = await this.authorizedFetch(
      `/api/v1/patients?limit=30${after ? `&after=${encodeURIComponent(after)}` : ""}`,
      { signal },
    );
    if (!response.ok) throw await this.apiError(response);
    return parsePatientPage(await responseJson(response));
  }

  async getPatientHealthProfile(
    patientId: string,
    signal?: AbortSignal,
  ): Promise<HealthProfile> {
    const response = await this.authorizedFetch(
      `/api/v1/patients/${encodeURIComponent(patientId)}/health-profile`,
      { signal },
    );
    if (!response.ok) throw await this.apiError(response);
    return parseHealthProfile(await responseJson(response));
  }

  async getPatientWearableDashboard(
    patientId: string,
    signal?: AbortSignal,
  ): Promise<WearableDashboard> {
    const response = await this.authorizedFetch(
      `/api/v1/patients/${encodeURIComponent(patientId)}/wearable-dashboard`,
      { signal },
    );
    if (!response.ok) throw await this.apiError(response);
    return parseWearableDashboard(await responseJson(response));
  }

  async getWearableDashboard(signal?: AbortSignal): Promise<WearableDashboard> {
    const response = await this.authorizedFetch(
      "/api/v1/health-profile/wearable-dashboard",
      { signal },
    );
    if (!response.ok) throw await this.apiError(response);
    return parseWearableDashboard(await responseJson(response));
  }

  async answerHealthProfileCard(
    cardId: string,
    clientActionId: string,
    answer: HealthProfileCardAnswer,
  ): Promise<HealthProfileCardDecision> {
    const customAnswer = answer.customAnswer;
    const body =
      customAnswer === undefined
        ? {
            client_action_id: clientActionId,
            option_id: answer.optionId,
          }
        : customAnswer.targetType === "personal_profile"
          ? {
              client_action_id: clientActionId,
              custom_answer: {
                value: customAnswer.value,
                ...(customAnswer.unit === undefined
                  ? {}
                  : { unit: customAnswer.unit }),
              },
            }
          : {
              client_action_id: clientActionId,
              custom_answer: {
                statement: customAnswer.statement,
                assertion: customAnswer.assertion,
                temporal_status: customAnswer.temporalStatus,
              },
            };
    const response = await this.authorizedFetch(
      `/api/v1/health-profile/cards/${encodeURIComponent(cardId)}/answer`,
      {
        method: "POST",
        body: JSON.stringify(body),
      },
    );
    if (response.status === 409) {
      const payload = await responseJson(response);
      try {
        const result = parseHealthProfileCardDecision(payload);
        if (result.status === "conflicted") return result;
      } catch {
        // A normal API error is handled below from the already-read payload.
      }
      const details = readErrorPayload(payload);
      throw new ApiError(
        details.message ?? "服务器返回 HTTP 409",
        response.status,
        details.code,
      );
    }
    if (!response.ok) throw await this.apiError(response);
    return parseHealthProfileCardDecision(await responseJson(response));
  }

  async changeHealthProfile(
    change: HealthProfileChange,
  ): Promise<HealthProfileChangeResult> {
    const body: Record<string, unknown> = {
      client_action_id: change.clientActionId,
      expected_revision: change.expectedRevision,
      target_type: change.targetType,
      operation: change.operation,
    };

    if ("fieldName" in change) body.field_name = change.fieldName;
    if ("targetId" in change) body.target_id = change.targetId;
    if ("value" in change) body.value = change.value;
    if ("unit" in change && change.unit !== undefined) body.unit = change.unit;
    if ("factType" in change) body.fact_type = change.factType;
    if ("statement" in change) body.statement = change.statement;
    if ("assertion" in change) body.assertion = change.assertion;
    if ("temporalStatus" in change) {
      body.temporal_status = change.temporalStatus;
    }

    const response = await this.authorizedFetch(
      "/api/v1/health-profile/changes",
      {
        method: "POST",
        body: JSON.stringify(body),
      },
    );
    if (!response.ok) throw await this.apiError(response);
    return parseHealthProfileChangeResult(await responseJson(response));
  }

  async importWearableRecords(
    clientSyncId: string,
    recordType: WearableRecord["record_type"],
    healthContextComplete: boolean,
    records: WearableRecord[],
    deletedRecordIds: string[],
    signal?: AbortSignal,
  ): Promise<WearableImportResponse> {
    const response = await this.authorizedFetch(
      "/api/v1/health-profile/wearable-imports",
      {
        method: "POST",
        body: JSON.stringify({
          client_sync_id: clientSyncId,
          record_type: recordType,
          health_context_complete: healthContextComplete,
          records,
          deleted_record_ids: deletedRecordIds,
        }),
        signal,
      },
    );
    if (!response.ok) throw await this.apiError(response);
    return parseWearableImportResponse(await responseJson(response));
  }

  async listHealthConnectRecordIds(
    recordType: WearableRecord["record_type"],
    after?: string,
    signal?: AbortSignal,
  ): Promise<HealthConnectRecordIdsPage> {
    const query = new URLSearchParams({ record_type: recordType });
    if (after !== undefined) query.set("after", after);
    const response = await this.authorizedFetch(
      `/api/v1/health-profile/wearable-health-connect-ids?${query.toString()}`,
      { signal },
    );
    if (!response.ok) throw await this.apiError(response);
    return parseHealthConnectRecordIdsPage(await responseJson(response));
  }

  async streamMessage(
    clientMessageId: string,
    content: string,
    attachmentId: string | null,
    onEvent: (event: ChatStreamEvent) => void,
    signal?: AbortSignal,
    liveHeartRate?: LiveHeartRateReading | null,
    conversationId: string | null = null,
  ): Promise<void> {
    const response = await this.authorizedFetch('/api/v1/chat/messages', {
      method: 'POST',
      body: JSON.stringify({
        client_message_id: clientMessageId,
        content,
        attachment_id: attachmentId,
        conversation_id: conversationId,
        live_heart_rate: liveHeartRate === undefined || liveHeartRate === null
          ? null
          : {
            beats_per_minute: liveHeartRate.beatsPerMinute,
            measured_at: liveHeartRate.measuredAt,
            device_name: liveHeartRate.deviceName,
          },
      }),
      signal,
      headers: { Accept: 'application/x-ndjson' },
    });

    await this.consumeChatResponse(response, onEvent);
  }

  async uploadChatAttachment(attachment: {
    kind: "photo" | "report";
    mimeType: string;
    name: string;
    uri: string;
  }): Promise<ChatAttachment> {
    const form = new FormData();
    const file = new File(attachment.uri);
    form.append("kind", attachment.kind);
    form.append("file", {
      bytes: () => file.bytes(),
      name: attachment.name,
      type: attachment.mimeType,
    } as unknown as Blob);
    const response = await this.authorizedFetch("/api/v1/chat/attachments", {
      method: "POST",
      body: form,
    });
    if (!response.ok) throw await this.apiError(response);
    return parseAttachmentUpload(await responseJson(response));
  }

  async retryMessage(
    failedAssistantMessageId: string,
    onEvent: (event: ChatStreamEvent) => void,
    signal?: AbortSignal,
  ): Promise<void> {
    const response = await this.authorizedFetch(
      `/api/v1/chat/messages/${encodeURIComponent(failedAssistantMessageId)}/retry`,
      {
        method: "POST",
        signal,
        headers: { Accept: "application/x-ndjson" },
      },
    );

    await this.consumeChatResponse(response, onEvent);
  }

  async deleteAccount(password: string): Promise<void> {
    const version = this.sessionVersion;
    const response = await this.authorizedFetch('/api/v1/auth/account', {
      method: 'DELETE',
      body: JSON.stringify({ password }),
    });
    this.assertSessionVersion(version);
    if (!response.ok) throw await this.apiError(response);
    await this.mutateTokens(version, () => this.tokenStore.clear());
  }

  async createMealLog(input: {
    eatenAt: string;
    mealType: "breakfast" | "lunch" | "dinner" | "snack";
    description?: string;
    carbsGrams?: number | null;
  }): Promise<MealLog> {
    const response = await this.authorizedFetch("/api/v1/health-profile/meal-logs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        eaten_at: input.eatenAt,
        meal_type: input.mealType,
        description: input.description ?? "",
        carbs_grams: input.carbsGrams ?? null,
      }),
    });
    if (!response.ok) throw await this.apiError(response);
    return parseMealLog(await responseJson(response));
  }

  async getMealLogs(days = 7, signal?: AbortSignal): Promise<MealLog[]> {
    const response = await this.authorizedFetch(
      `/api/v1/health-profile/meal-logs?days=${days}`,
      { signal },
    );
    if (!response.ok) throw await this.apiError(response);
    return parseMealLogs(await responseJson(response));
  }

  async deleteMealLog(id: string): Promise<void> {
    const response = await this.authorizedFetch(
      `/api/v1/health-profile/meal-logs/${id}`,
      { method: "DELETE" },
    );
    if (!response.ok) throw await this.apiError(response);
  }

  /**
   * 导出患者自己的全部记录。返回原文 + 服务器给的 content-disposition，
   * 文件名解析、落盘与分享交给调用方，这样 API 层不依赖 expo-file-system。
   */
  async exportMyData(): Promise<{ contentDisposition: string | null; body: string }> {
    const response = await this.authorizedFetch("/api/v1/health-profile/export");
    if (!response.ok) throw await this.apiError(response);
    return {
      contentDisposition: response.headers.get("content-disposition"),
      body: await response.text(),
    };
  }

  async createMedicationLog(input: {
    takenAt: string;
    name: string;
    doseText?: string;
    kind?: "insulin" | "oral" | "other";
    note?: string | null;
  }): Promise<MedicationLog> {
    const response = await this.authorizedFetch(
      "/api/v1/health-profile/medication-logs",
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          taken_at: input.takenAt,
          name: input.name,
          dose_text: input.doseText ?? "",
          kind: input.kind ?? "other",
          note: input.note ?? null,
        }),
      },
    );
    if (!response.ok) throw await this.apiError(response);
    return parseMedicationLog(await responseJson(response));
  }

  async getMedicationLogs(days = 30, signal?: AbortSignal): Promise<MedicationLog[]> {
    const response = await this.authorizedFetch(
      `/api/v1/health-profile/medication-logs?days=${days}`,
      { signal },
    );
    if (!response.ok) throw await this.apiError(response);
    return parseMedicationLogs(await responseJson(response));
  }

  async deleteMedicationLog(id: string): Promise<void> {
    const response = await this.authorizedFetch(
      `/api/v1/health-profile/medication-logs/${id}`,
      { method: "DELETE" },
    );
    if (!response.ok) throw await this.apiError(response);
  }

  async getPsychosocialInstrument(): Promise<PsychosocialInstrument> {
    const response = await this.authorizedFetch(
      '/api/v1/health-profile/psychosocial-instrument',
    );
    if (!response.ok) throw await this.apiError(response);
    return parsePsychosocialInstrument(await responseJson(response));
  }

  async getPsychosocialAssessment(): Promise<PsychosocialProfile | null> {
    const response = await this.authorizedFetch(
      '/api/v1/health-profile/psychosocial-assessment',
    );
    if (!response.ok) throw await this.apiError(response);
    return parsePsychosocialProfile(await responseJson(response));
  }

  async recordPsychosocialAssessment(
    input: PsychosocialAssessmentInput,
  ): Promise<PsychosocialProfile> {
    const response = await this.authorizedFetch(
      '/api/v1/health-profile/psychosocial-assessment',
      {
        method: 'POST',
        body: JSON.stringify({
          answers: input.answers,
          fear_behavior_percent: input.fearBehaviorPercent,
          fear_worry_percent: input.fearWorryPercent,
          gold_score: input.goldScore,
          instrument: input.instrument,
          assessed_at: input.assessedAt,
        }),
      },
    );
    if (!response.ok) throw await this.apiError(response);
    const parsed = parsePsychosocialProfile(await responseJson(response));
    if (parsed === null) throw new Error('服务器没有返回沟通方式评估结果');
    return parsed;
  }

  async getFearSummary(signal?: AbortSignal): Promise<FearSummary> {
    const response = await this.authorizedFetch(
      '/api/v1/health-profile/fear-summary',
      { signal },
    );
    if (!response.ok) throw await this.apiError(response);
    return parseFearSummary(await responseJson(response));
  }

  async getFearBehaviorLogs(signal?: AbortSignal): Promise<FearBehaviorLog[]> {
    const response = await this.authorizedFetch(
      '/api/v1/health-profile/fear-behavior',
      { signal },
    );
    if (!response.ok) throw await this.apiError(response);
    return parseFearBehaviorLogs(await responseJson(response));
  }

  async getBeliefEvents(signal?: AbortSignal): Promise<BeliefEventRecord[]> {
    const response = await this.authorizedFetch(
      '/api/v1/health-profile/belief-events',
      { signal },
    );
    if (!response.ok) throw await this.apiError(response);
    return parseBeliefEvents(await responseJson(response));
  }

  async deleteFearBehaviorLog(id: string): Promise<void> {
    const response = await this.authorizedFetch(
      `/api/v1/health-profile/fear-behavior/${id}`,
      { method: 'DELETE' },
    );
    if (!response.ok) throw await this.apiError(response);
  }

  async deleteBeliefEvent(id: string): Promise<void> {
    const response = await this.authorizedFetch(
      `/api/v1/health-profile/belief-events/${id}`,
      { method: 'DELETE' },
    );
    if (!response.ok) throw await this.apiError(response);
  }

  /**
   * 传感器导出文件先解析、不落库：给患者看清楚多少行能用、哪些行有问题，再决定导不导。
   */
  async previewGlucoseImport(
    file: { mimeType: string; name: string; uri: string },
    sourceSystem: CgmSourceSystem = "generic",
  ): Promise<CgmImportPreview> {
    const response = await this.authorizedFetch(
      `/api/v1/health-profile/glucose-imports/preview?source_system=${sourceSystem}`,
      { body: uploadForm(file), method: "POST" },
    );
    if (!response.ok) throw await this.apiError(response);
    return parseCgmImportPreview(await responseJson(response));
  }

  async importGlucoseCsv(
    file: { mimeType: string; name: string; uri: string },
    sourceSystem: CgmSourceSystem = "generic",
  ): Promise<CgmImportResult> {
    const response = await this.authorizedFetch(
      `/api/v1/health-profile/glucose-imports?source_system=${sourceSystem}`,
      { body: uploadForm(file), method: "POST" },
    );
    if (!response.ok) throw await this.apiError(response);
    return parseCgmImportResult(await responseJson(response));
  }

  async getCgmSamples(days = 7): Promise<CgmSeries> {
    const response = await this.authorizedFetch(
      `/api/v1/health-profile/cgm-samples?days=${days}`,
    );
    if (!response.ok) throw await this.apiError(response);
    return parseCgmSeries(await responseJson(response));
  }

  async getGlucoseReports(limit = 5): Promise<GlucoseReport[]> {
    const response = await this.authorizedFetch(
      `/api/v1/health-profile/glucose-reports?limit=${limit}`,
    );
    if (!response.ok) throw await this.apiError(response);
    return parseGlucoseReports(await responseJson(response));
  }

  async deleteGlucoseReport(id: string): Promise<void> {
    const response = await this.authorizedFetch(
      `/api/v1/health-profile/glucose-reports/${id}`,
      { method: "DELETE" },
    );
    if (!response.ok) throw await this.apiError(response);
  }

  /**
   * 选一份报告 PDF，先看服务端读到了什么。不落库——读错了还能改，读不到也会直说。
   */
  async previewGlucoseReport(file: {
    mimeType: string;
    name: string;
    uri: string;
  }): Promise<GlucoseReportPreview> {
    const response = await this.authorizedFetch(
      "/api/v1/health-profile/glucose-reports/preview",
      { body: uploadForm(file), method: "POST" },
    );
    if (!response.ok) throw await this.apiError(response);
    return parseGlucoseReportPreview(await responseJson(response));
  }

  /**
   * 患者确认之后才写库：把**这份文件**再传一遍。
   *
   * 存下来的不是预览里那几个数字，而是服务端重新按模板读一遍的结果——
   * 文件本身也留在服务端，所以详情页的「打开原报告」有得开。
   */
  async createGlucoseReport(file: {
    mimeType: string;
    name: string;
    uri: string;
  }): Promise<GlucoseReport> {
    const response = await this.authorizedFetch(
      "/api/v1/health-profile/glucose-reports",
      { body: uploadForm(file), method: "POST" },
    );
    if (!response.ok) throw await this.apiError(response);
    return parseGlucoseReport(await responseJson(response));
  }

  async transcribeVoice(recording: {
    mimeType: string;
    name: string;
    uri: string;
  }): Promise<string> {
    const form = new FormData();
    const file = new File(recording.uri);
    form.append(
      'file',
      {
        bytes: () => file.bytes(),
        name: recording.name,
        type: recording.mimeType,
      } as unknown as Blob,
    );
    const response = await this.authorizedFetch('/api/v1/voice/transcribe', {
      method: 'POST',
      body: form,
    });
    if (!response.ok) throw await this.apiError(response);
    return parseTranscription(await responseJson(response));
  }

  async createGlucoseReading(input: {
    valueMgDl: number;
    measuredAt: string;
    context: string;
    source?: string;
    sourceDevice?: string;
    note?: string | null;
  }): Promise<GlucoseReading> {
    const response = await this.authorizedFetch(
      "/api/v1/health-profile/glucose-readings",
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          value_mg_dl: input.valueMgDl,
          measured_at: input.measuredAt,
          context: input.context,
          source: input.source ?? "fingerstick",
          source_device: input.sourceDevice ?? null,
          note: input.note ?? null,
        }),
      },
    );
    if (!response.ok) throw await this.apiError(response);
    return parseGlucoseReading(await responseJson(response));
  }

  async getGlucoseReadings(days = 14, signal?: AbortSignal): Promise<GlucoseReading[]> {
    const response = await this.authorizedFetch(
      `/api/v1/health-profile/glucose-readings?days=${days}`,
      { signal },
    );
    if (!response.ok) throw await this.apiError(response);
    return parseGlucoseReadings(await responseJson(response));
  }

  async getGlucoseSummary(days = 7, signal?: AbortSignal): Promise<GlucoseSummary> {
    const response = await this.authorizedFetch(
      `/api/v1/health-profile/glucose-readings/summary?days=${days}`,
      { signal },
    );
    if (!response.ok) throw await this.apiError(response);
    return parseGlucoseSummary(await responseJson(response));
  }

  /** 患者自己的血糖目标范围；读到就用它，读不到（旧后端）就当 70–180。 */
  async getGlucoseTarget(signal?: AbortSignal): Promise<GlucoseTarget> {
    const response = await this.authorizedFetch(
      "/api/v1/health-profile/glucose-target",
      { signal },
    );
    if (!response.ok) throw await this.apiError(response);
    return parseGlucoseTarget(await responseJson(response));
  }

  async setGlucoseTarget(
    lowMgDl: number,
    highMgDl: number,
    signal?: AbortSignal,
  ): Promise<GlucoseTarget> {
    const response = await this.authorizedFetch(
      "/api/v1/health-profile/glucose-target",
      {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ low_mg_dl: lowMgDl, high_mg_dl: highMgDl }),
        signal,
      },
    );
    if (!response.ok) throw await this.apiError(response);
    return parseGlucoseTarget(await responseJson(response));
  }

  async revokeGlucoseReading(id: string): Promise<void> {
    const response = await this.authorizedFetch(
      `/api/v1/health-profile/glucose-readings/${id}`,
      { method: "DELETE" },
    );
    if (!response.ok) throw await this.apiError(response);
  }
}
