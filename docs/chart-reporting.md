# Regenerate recorded charts

Run `python3 -m bench.report` to refresh the README, appendix and SVG images from
`results/trials.jsonl`. `python3 -m bench.charts` regenerates only the chart gallery
without executing a benchmark or installing a plotting dependency.

`docs/charts/` is generated output. It contains a summary, the latest recorded
leaderboard for each task, one history chart per harness and a linked archive of
each recorded trial. Tables and full settings remain available in `RESULTS.md`
and the JSONL source. Nightly publishing includes the generated directory; its
verification and CI reject modified, missing or stale images. Different leaderboards may describe different experiments;
they are never pooled into one ranking. Lines connect matching task, suite,
memory and execution settings only. Missing scores have no bar or zero point.

SVGs use a transparent background and light/dark text styles. Series use a calm
blue, teal, olive, gold and mauve palette with neutral text and guides. All scored
values are labeled; colors are consistent for a harness. Source rows are embedded
as metadata so the chart can be checked against its recorded inputs.

Removing the renderer, its tests and this guide rolls back standalone chart
generation without changing benchmark records or execution.
