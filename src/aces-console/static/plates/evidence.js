// Evidence plate: a placeholder so the shell loads. Builder D delivers the real plates/evidence.js
// (golden runs and run detail + Verify with "no grade is a sign-off", the desktop ledger + Verify ledger, the SIMLAB chain; spec 4.3).
export async function mount(root, ctx) {
  root.append(ctx.dom.note("plate not built yet", "Builder D delivers plates/evidence.js (golden runs and run detail with Verify, the desktop ledger with Verify ledger, the SIMLAB evidence chain). The shell loads without it.", { detail: "spec 4.3 Evidence" }));
  root.firstElementChild.dataset.placeholder = "evidence";
  ctx.coord("not built");
}
export function unmount() {}
