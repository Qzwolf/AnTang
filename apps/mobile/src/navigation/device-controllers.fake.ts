/**
 * 测试用的手环控制器替身。
 *
 * 真控制器会调用 Health Connect 原生模块和文件选择器，单测里都不能碰；这里只提供
 * 界面会读到的字段，让「记录页 / 聊天页」的测试能聚焦在展示与交互上。
 */
import type {
  GadgetbridgeController,
  HealthConnectController,
} from './device-channels';

export function fakeHealthConnect(
  overrides: Partial<HealthConnectController> = {},
): HealthConnectController {
  return {
    connected: false,
    checking: false,
    syncing: false,
    error: null,
    progress: null,
    lastSyncedAt: null,
    backgroundStatus: 'disabled',
    backgroundEnabling: false,
    backgroundDisabling: false,
    backgroundError: null,
    connect: jest.fn(),
    sync: jest.fn(),
    stop: jest.fn(),
    enableBackground: jest.fn(async () => undefined),
    disableBackground: jest.fn(async () => undefined),
    ...overrides,
  } as unknown as HealthConnectController;
}

export function fakeGadgetbridge(
  overrides: Partial<GadgetbridgeController> = {},
): GadgetbridgeController {
  return {
    connected: false,
    checking: false,
    syncing: false,
    error: null,
    progress: null,
    heartRateCount: 0,
    lastSyncedAt: null,
    lastBundle: null,
    connect: jest.fn(),
    sync: jest.fn(),
    stop: jest.fn(),
    ...overrides,
  } as unknown as GadgetbridgeController;
}
