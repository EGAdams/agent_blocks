# Terminal components

The `log-viewer` and `log-object` component TypeScript, HTML, and local CSS
were copied from `/home/adamsl/the-factory/src/components/` before adaptation.
Source repository: https://github.com/EGAdams/the-factory
Source revision: `61c97b7c82c024dc291ba6720e0fbf35385b8f8c` (ISC license).

These local copies use native custom elements and Shadow DOM in place of the
factory's decorator/build aliases. `LogViewer.logs` accepts typed entries from
`EventTerminal.vue`; the existing session poll supplies all recorded events
every 500 ms. There is no PHP API, separate polling loop, or runtime import
from the source repository.

All recorded payloads are displayed automatically, including tool arguments
and recorded result snippets. Invalid JSON is displayed literally. Messages
use `textContent`, never HTML interpretation. Existing rows are retained by ID
while new entries append. The terminal starts following the latest entry;
scrolling up pauses following, and the button or scrolling to the bottom resumes
it. No events are removed from the run by pausing or leaving this view.

The entire session appears even when a phase is selected. Logs are also visible
without selecting a phase. This displays recorded events as they arrive; it
cannot show subprocess output that the workflow did not record.
