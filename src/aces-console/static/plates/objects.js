// Objects plate: a placeholder so the shell loads. Builder D delivers the real plates/objects.js
// (CAD runs, run detail, drawings, bundles + Check bundle, notes, STL viewer, Onshape card; spec 4.3).
export async function mount(root, ctx) {
  root.append(ctx.dom.note("plate not built yet", "Builder D delivers plates/objects.js (CAD runs, run detail, drawings, bundles with Check bundle, notes, the STL viewer, the Onshape card). The shell loads without it.", { detail: "spec 4.3 Objects" }));
  root.firstElementChild.dataset.placeholder = "objects";
  ctx.coord("not built");
}
export function unmount() {}
