import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { ConfirmDialog } from "./ui";

describe("ConfirmDialog", () => {
  it("moves focus inside, traps Tab and closes with Escape", async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    render(<ConfirmDialog open title="确认操作" description={<p>请确认</p>} onClose={onClose} onConfirm={() => undefined} />);
    const cancel = screen.getByRole("button", { name: "取消" });
    const confirm = screen.getByRole("button", { name: "确认" });
    expect(cancel).toHaveFocus();
    await user.tab({ shift: true });
    expect(confirm).toHaveFocus();
    await user.keyboard("{Escape}");
    expect(onClose).toHaveBeenCalledOnce();
  });
});
