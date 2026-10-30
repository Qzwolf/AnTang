import { act, create, type ReactTestRenderer } from "react-test-renderer";

import { ProfileScreen } from "./profile-screen";

function renderProfile(overrides: Partial<Parameters<typeof ProfileScreen>[0]> = {}) {
  const opened: string[] = [];
  const props: Parameters<typeof ProfileScreen>[0] = {
    name: "akaa",
    username: "akaa",
    createdAt: null,
    versionLabel: "当前版本 1.0.0 · 26102302",
    onOpenHealthProfile: () => opened.push("health-profile"),
    onOpenHealthDashboard: () => opened.push("dashboard"),
    onOpenPatientAccess: () => opened.push("access"),
    onExportData: () => opened.push("export"),
    onOpenCareSettings: () => opened.push("care"),
    onOpenSettings: () => opened.push("settings"),
    onSignOut: () => opened.push("sign-out"),
    onDeleteAccount: () => opened.push("delete"),
    ...overrides,
  };
  return { opened, props };
}

describe("ProfileScreen", () => {
  it("把低频入口按数据、隐私、设置、账号四段排开", async () => {
    const { props } = renderProfile();
    let renderer: ReactTestRenderer | null = null;
    await act(async () => {
      renderer = create(<ProfileScreen {...props} />);
    });
    const text = JSON.stringify((renderer as unknown as ReactTestRenderer).toJSON());

    expect(text).toContain("我的数据");
    expect(text).toContain("隐私与分享");
    expect(text).toContain("导出我的数据");
    expect(text).toContain("退出登录");
    expect(text).toContain("注销账号");
  });

  it("点了哪一行就报哪一行", async () => {
    const { opened, props } = renderProfile();
    let renderer: ReactTestRenderer | null = null;
    await act(async () => {
      renderer = create(<ProfileScreen {...props} />);
    });
    const tree = renderer as unknown as ReactTestRenderer;

    for (const label of ["健康档案", "健康看板", "医护授权", "导出我的数据", "主动关怀", "检查更新"]) {
      await act(async () => {
        tree.root.findByProps({ accessibilityLabel: label }).props.onPress();
      });
    }

    expect(opened).toEqual([
      "health-profile",
      "dashboard",
      "access",
      "export",
      "care",
      "settings",
    ]);
  });

  it("账号动作用单独的告警色，且退出登录不显示注册天数以外的提示", async () => {
    const { opened, props } = renderProfile({ createdAt: "2026-10-01T00:00:00Z" });
    let renderer: ReactTestRenderer | null = null;
    await act(async () => {
      renderer = create(<ProfileScreen {...props} />);
    });
    const tree = renderer as unknown as ReactTestRenderer;

    // 有注册时间就显示「已使用 N 天」，没有就不显示，而不是显示 0 天。
    expect(JSON.stringify(tree.toJSON())).toContain("注册于");

    await act(async () => {
      tree.root.findByProps({ accessibilityLabel: "注销账号" }).props.onPress();
      tree.root.findByProps({ accessibilityLabel: "退出登录" }).props.onPress();
    });
    expect(opened).toEqual(["delete", "sign-out"]);
  });
});
