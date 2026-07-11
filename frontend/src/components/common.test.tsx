import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { Pagination, StatusBadge, statusLabel } from "./common";

describe("StatusBadge", () => {
  it("distinguishes partial failures from system failures", () => {
    const { rerender } = render(<StatusBadge status="failed" />);
    expect(screen.getByText("有失败")).toBeInTheDocument();
    rerender(<StatusBadge status="failed_system" />);
    expect(screen.getByText("系统故障")).toBeInTheDocument();
  });

  it("keeps unknown backend states visible", () => {
    expect(statusLabel("half_open")).toBe("half_open");
  });
});

describe("Pagination", () => {
  it("supports page buttons, next page and direct jump", async () => {
    const user = userEvent.setup();
    const changes: number[] = [];
    const { rerender } = render(<Pagination page={1} totalItems={95} onChange={(page) => changes.push(page)} />);
    await user.click(screen.getByRole("button", { name: "第 2 页" }));
    await user.click(screen.getByRole("button", { name: "下一页" }));
    await user.clear(screen.getByRole("textbox", { name: "跳转页码" }));
    await user.type(screen.getByRole("textbox", { name: "跳转页码" }), "7");
    await user.click(screen.getByRole("button", { name: "跳转" }));
    expect(changes).toEqual([2, 2, 7]);
    rerender(<Pagination page={10} totalItems={95} onChange={() => undefined} />);
    expect(screen.getByRole("button", { name: "下一页" })).toBeDisabled();
  });
});
