import { act, create, type ReactTestRenderer } from "react-test-renderer";

import { AppTabBar } from "./tab-bar";

describe("AppTabBar", () => {
  it("marks the active tab and reports the one that was pressed", async () => {
    const changes: string[] = [];
    let renderer: ReactTestRenderer | null = null;
    await act(async () => {
      renderer = create(
        <AppTabBar active="chat" onChange={(key) => changes.push(key)} />,
      );
    });
    const tree = renderer as unknown as ReactTestRenderer;

    const chat = tree.root.findByProps({ accessibilityLabel: "对话标签页" });
    expect(chat.props.accessibilityState.selected).toBe(true);
    const records = tree.root.findByProps({ accessibilityLabel: "记录标签页" });
    expect(records.props.accessibilityState.selected).toBe(false);

    await act(async () => {
      records.props.onPress();
    });
    expect(changes).toEqual(["records"]);
  });
});
