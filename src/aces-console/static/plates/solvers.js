// Solvers plate: a placeholder so the shell loads. Builder D delivers the real plates/solvers.js
// (registry with citability, capacity with activity labels, mode pools; spec 4.3).
export async function mount(root, ctx) {
  root.append(ctx.dom.note("plate not built yet", "Builder D delivers plates/solvers.js (solver registry with citability, capacity with activity labels, mode pools). The shell loads without it.", { detail: "spec 4.3 Solvers" }));
  root.firstElementChild.dataset.placeholder = "solvers";
  ctx.coord("not built");
}
export function unmount() {}
