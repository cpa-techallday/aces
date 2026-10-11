// Pipeline plate: a placeholder so the shell loads. Builder D delivers the real plates/pipeline.js
// (rig experiments, queue, cases, case detail, files, MLflow run; spec 4.3).
export async function mount(root, ctx) {
  root.append(ctx.dom.note("plate not built yet", "Builder D delivers plates/pipeline.js (rig experiments, queue, cases, case detail, files, MLflow run). The shell loads without it.", { detail: "spec 4.3 Pipeline" }));
  root.firstElementChild.dataset.placeholder = "pipeline";
  ctx.coord("not built");
}
export function unmount() {}
