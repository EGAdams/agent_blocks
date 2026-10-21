const content = document.getElementById( "content" );
const nav = document.getElementById( "nav" );

// When hosted inside the main dashboard, the parent mirrors this live nav in
// its own sidebar. Keep only the documentation content inside the iframe.
if ( new URLSearchParams( window.location.search ).get( "embedded" ) === "1" ) {
  document.body.classList.add( "embedded" );
}

// Mirrors the directory layout under spa_documentation/. Add new sections
// and items here as their skeleton folders get built out.
const sections = {
  claude_agent_adapter: {
    label: "Claude Agent Adapter",
    items: {
      adapter: { label: "Claude Adapter" },
    },
  },
  catherine_agent_sdk: {
    label: "Catherine Agent",
    items: {
      basic_agent: { label: "Basic Agent" },
      future_agent: { label: "Future Agent" },
    },
  },
  lancedb_memory: {
    label: "Lancedb Memory",
    items: {
      agent: { label: "Agent (Orchestrator)" },
      config: { label: "Config" },
      models: {
        label: "Models",
        items: {
          turn: { label: "Turn" },
          embedded_turn: { label: "EmbeddedTurn" },
          recalled_turn: { label: "RecalledTurn" },
          memory_query: { label: "MemoryQuery" },
          agent_reply: { label: "AgentReply" },
        },
      },
      ports: { label: "Ports" },
      clock: { label: "Clock" },
      observers: {
        label: "Observers",
        items: {
          null_observer: { label: "Null Observer" },
          composite_observer: { label: "Composite Observer" },
          console_observer: { label: "Console Observer" },
          cost_observer: { label: "Cost Observer" },
          nexus_archive_observer: { label: "Nexus Archive Observer" },
        },
      },
      prompts: { label: "Prompts" },
      recall: { label: "Recall" },
      adapters: {
        label: "Adapters",
        items: {
          claude_model: { label: "Claude Model Adapter" },
          decorators: { label: "Decorators Adapter" },
          embedding_profiles: { label: "Embedding Profiles Adapter" },
          hashing_embedder: { label: "Hashing Embedder Adapter" },
          in_memory_store: { label: "In-Memory Store Adapter" },
          lancedb_store: { label: "LanceDB Store Adapter" },
          openai_embedder: { label: "OpenAI Embedder Adapter" },
          sentence_embedder: { label: "Sentence Embedder Adapter" },
        },
      },
    },
  },
  voice_communication: {
    label: "Voice Communication",
    items: {
      interfaces: { label: "Main Interface" },
      typescript_contracts: { label: "TypeScript Contracts" },
      pydantic_models: { label: "Pydantic Models" },
      input_draft: { label: "Input Draft" },
      voice_session: { label: "Voice Session" },
      conversation_agent: { label: "IConversationAgent" },
      spoken_output_policy: { label: "Spoken Output Policy" },
      conversation_coordinator: { label: "Conversation Coordinator" },
      letta_agent_adapter: { label: "Letta Agent Adapter" },
      audio_capture: { label: "Audio Capture" },
      transcription_strategy: { label: "Transcription Strategy" },
      route_strategy: { label: "Route Strategy" },
      speech_synthesizer: { label: "Speech Synthesizer" },
      note_command_channel: { label: "Note Command Channel" },
      detection_interface: { label: "Detection Interface" },
      language_processor: { label: "Language Processor" },
      design_protocol: { label: "Design Protocol" },
      pipecat_service_client: { label: "Pipecat Service Client" },
      pipecat_local_service: { label: "Pipecat Local Service" },
      pipeline_factory: { label: "Pipeline Factory" },
      toyota_voice_application: { label: "Toyota Voice Application" },
      voice_health_observer: { label: "Voice Health Observer" },
    },
  },
  spa_browser_tests: {
    label: "Browser Tests",
    items: {
      characterization_suite: { label: "Characterization Suite" },
    },
  },
  mazda: {
    label: "Mazda",
    items: {
      agent: { label: "Mazda Agent" },
      finance_orchestrator: { label: "Finance Orchestrator" },
      intake_dispatch: { label: "Intake Dispatch" },
      intake_evidence: { label: "Intake Evidence" },
      trainer_escalation: { label: "Trainer Escalation" },
      runtime_family: { label: "Runtime Family" },
      runtime_kernel: { label: "Runtime Kernel" },
      persistence_factory: { label: "Persistence Factory" },
      llm_factory: { label: "LLM Factory" },
      prompt_factory: { label: "Prompt Factory" },
      tool_factory: { label: "Tool Factory" },
      context_factory: { label: "Context Factory" },
      workflow_factory: { label: "Workflow Factory" },
      evaluation_factory: { label: "Evaluation Factory" },
      improvement_factory: { label: "Improvement Factory" },
      reporting_factory: { label: "Reporting Factory" },
      agent_package_factory: { label: "Agent Package Factory" },
    },
  },
  solid_agent_systems: {
    label: "Solid Agent Systems",
    items: {},
  },
};

// Every item folder currently uses these same file basenames (see
// catherine_agent_sdk/basic_agent and .../future_agent).
const overviewFile = "basic_agent.html";
// Absolute location of this checkout, shown under each construction lesson's
// title so a reader knows which file on disk to edit. Both this workstation
// and the dashboard host keep the repo at the same path; change it here only.
const DOC_ROOT = "/home/adamsl/agent_blocks/spa_documentation";
const detailTabs = [
  { key: "source", label: "Source", file: "basic_agent.py.html" },
  { key: "class", label: "Class Diagram", file: "mermaid_class.html" },
  { key: "sequence", label: "Sequence Diagram", file: "mermaid_sequence.html" },
  { key: "status", label: "Construction Status", file: "basic_agent_construction_status.html" },
  // The report body here is static (written by update_documentation_agent.py
  // when run from the terminal), but the button above it is live: on load,
  // app.js hits server.py's /api/git-status route to enable/disable it.
  { key: "update_docs", label: "Update Documentation", file: "basic_agent_update_documentation.html" },
];

// Keyed by "top/path/to/item" -> { exists, dirty }. Refetched every time a
// detail tab page loads (see selectDetail) -- not polled on a timer.
let gitStatusCache = {};

let currentTop = "home";
// Path of keys drilling down through nested `items` from the section root
// (e.g. [ "adapters", "claude_model" ]). Empty means "at the section root".
// A resolved node is a *folder* if it has an `items` map (fans out further,
// mirroring a directory in `tree`), or a *leaf* if it doesn't (has detail tabs).
let itemPath = [];
let currentDetail = null;

// Construction Status pages may opt into a TaskMaster-style drill-down by
// including a semantic .construction-task-tree. null means the normal fixed
// detail tabs are visible; an array means the task nav is active at that path.
let constructionTaskPath = null;
let focusedConstructionTask = null;

// innerHTML does not execute <script> tags, so detail pages that need JS
// (e.g. mermaid diagrams) would otherwise silently do nothing. Re-create
// each script node so the browser actually runs it.
function executeScripts( container ) {
  container.querySelectorAll( "script" ).forEach( ( oldScript ) => {
    const newScript = document.createElement( "script" );
    Array.from( oldScript.attributes ).forEach( ( attr ) => {
      newScript.setAttribute( attr.name, attr.value );
    } );
    newScript.textContent = oldScript.textContent;
    oldScript.replaceWith( newScript );
  } );
}

// Clipboard writes on Android Chrome are unreliable through the async
// Clipboard API alone (it silently no-ops outside a secure context, and some
// WebViews withhold the permission); a hidden-textarea + execCommand("copy")
// fallback is what actually lands the text on the clipboard there.
function copyTextToClipboard( text, onDone ) {
  if ( window.isSecureContext && navigator.clipboard && navigator.clipboard.writeText ) {
    navigator.clipboard.writeText( text ).then( onDone ).catch( () => legacyCopyToClipboard( text, onDone ) );
  } else {
    legacyCopyToClipboard( text, onDone );
  }
}

function legacyCopyToClipboard( text, onDone ) {
  const textarea = document.createElement( "textarea" );
  textarea.value = text;
  textarea.setAttribute( "readonly", "" );
  textarea.style.position = "fixed";
  textarea.style.left = "-1000px";
  document.body.appendChild( textarea );
  textarea.select();
  textarea.setSelectionRange( 0, text.length );
  try {
    document.execCommand( "copy" );
  } catch ( err ) {
    // Nothing more we can do; the button label simply won't confirm.
  }
  document.body.removeChild( textarea );
  onDone();
}

// Mermaid figures inside an authored lesson. This used to be an inline
// <script> in every lesson page; it lives here so the pages stay short and
// only one copy has to be kept correct.
const MERMAID_THEME = {
  startOnLoad: false, securityLevel: "loose", theme: "base",
  themeVariables: {
    lineColor: "#b58b36", primaryColor: "#17365d", primaryTextColor: "#ffffff",
    primaryBorderColor: "#0d2744", secondaryColor: "#eaf2fb", tertiaryColor: "#f8f2e4",
    // Sequence message text sits on the page background and needs dark ink;
    // class-diagram labels sit inside the navy boxes and need light ink. They
    // are two different variables for exactly that reason.
    textColor: "#172033", classText: "#ffffff",
    actorBkg: "#17365d", actorBorder: "#0d2744", actorTextColor: "#ffffff",
    actorLineColor: "#8fa2ba", signalColor: "#17365d", signalTextColor: "#172033",
    sequenceNumberColor: "#ffffff", noteBkgColor: "#f8f2e4", noteBorderColor: "#b58b36",
    noteTextColor: "#172033", labelBoxBkgColor: "#eaf2fb", labelTextColor: "#172033",
  },
};

// Vendored, and deliberately relative: this SPA is also served under a
// reverse-proxy subpath, where "/vendor/..." would resolve against the
// proxy's own root and 404.
function ensureMermaidLoaded() {
  if ( window.mermaid ) return Promise.resolve();
  if ( !window.__mermaidLoading ) {
    window.__mermaidLoading = new Promise( ( resolve, reject ) => {
      const script = document.createElement( "script" );
      script.src = "vendor/mermaid.min.js";
      script.onload = resolve;
      script.onerror = reject;
      document.head.appendChild( script );
    } );
  }
  return window.__mermaidLoading;
}

function wireDiagramPanZoom( figure ) {
  const viewport = figure.querySelector( ".mermaid-viewport" );
  const canvas = figure.querySelector( ".mermaid-canvas" );
  let scale = 1, x = 0, y = 0, dragging = false, startX = 0, startY = 0;
  const apply = () => { canvas.style.transform = `translate(${x}px, ${y}px) scale(${scale})`; };
  const reset = () => { scale = 1; x = 0; y = 0; apply(); };
  const zoomAt = ( clientX, clientY, factor ) => {
    const rect = viewport.getBoundingClientRect();
    const px = clientX - rect.left, py = clientY - rect.top;
    const next = Math.min( 4, Math.max( 0.3, scale * factor ));
    if ( next === scale ) return;
    x = px - ( px - x ) * ( next / scale );
    y = py - ( py - y ) * ( next / scale );
    scale = next;
    apply();
  };
  viewport.addEventListener( "wheel", ( event ) => {
    event.preventDefault();
    if ( event.shiftKey ) { x -= event.deltaY; apply(); return; }
    zoomAt( event.clientX, event.clientY, Math.exp( -event.deltaY * 0.0015 ));
  }, { passive: false } );
  viewport.addEventListener( "pointerdown", ( event ) => {
    dragging = true;
    startX = event.clientX - x;
    startY = event.clientY - y;
    viewport.classList.add( "dragging" );
    viewport.setPointerCapture( event.pointerId );
  } );
  viewport.addEventListener( "pointermove", ( event ) => {
    if ( !dragging ) return;
    x = event.clientX - startX;
    y = event.clientY - startY;
    apply();
  } );
  const stop = ( event ) => {
    dragging = false;
    viewport.classList.remove( "dragging" );
    if ( viewport.hasPointerCapture?.( event.pointerId )) viewport.releasePointerCapture( event.pointerId );
  };
  viewport.addEventListener( "pointerup", stop );
  viewport.addEventListener( "pointercancel", stop );
  viewport.addEventListener( "dblclick", reset );
  figure.querySelectorAll( ".diagram-controls button" ).forEach( ( button ) => {
    button.addEventListener( "click", () => {
      const rect = viewport.getBoundingClientRect();
      const cx = rect.left + rect.width / 2, cy = rect.top + rect.height / 2;
      if ( button.dataset.action === "in" ) zoomAt( cx, cy, 1.25 );
      else if ( button.dataset.action === "out" ) zoomAt( cx, cy, 0.8 );
      else reset();
    } );
  } );
}

function wireSequenceStepTooltips( figure ) {
  const explanations = Array.from(
    figure.querySelectorAll( ".diagram-step-tooltips li" ),
    ( item ) => item.textContent.trim(),
  );
  if ( explanations.length === 0 ) return;

  const numbers = figure.querySelectorAll( "svg .sequenceNumber" );
  numbers.forEach( ( number, index ) => {
    const explanation = explanations[ index ];
    if ( !explanation ) return;

    const step = number.textContent.trim();
    const label = `Step ${step}: ${explanation}`;
    const tooltipId = `diagram-step-tooltip-${figure.dataset.diagram}-${index + 1}`;
    let tooltip = null;

    const positionTooltip = ( clientX, clientY ) => {
      if ( !tooltip ) return;
      const edge = 12;
      const gap = 14;
      const rect = tooltip.getBoundingClientRect();
      let left = clientX + gap;
      let top = clientY + gap;
      if ( left + rect.width > window.innerWidth - edge ) {
        left = clientX - rect.width - gap;
      }
      if ( top + rect.height > window.innerHeight - edge ) {
        top = clientY - rect.height - gap;
      }
      tooltip.style.left = `${Math.max( edge, left )}px`;
      tooltip.style.top = `${Math.max( edge, top )}px`;
      tooltip.style.visibility = "visible";
    };
    const showTooltip = ( event ) => {
      if ( tooltip ) return;
      tooltip = document.createElement( "div" );
      tooltip.id = tooltipId;
      tooltip.className = "diagram-step-tooltip";
      tooltip.setAttribute( "role", "tooltip" );
      tooltip.textContent = label;
      tooltip.style.visibility = "hidden";
      document.body.appendChild( tooltip );
      const numberRect = number.getBoundingClientRect();
      positionTooltip(
        Number.isFinite( event.clientX ) ? event.clientX : numberRect.right,
        Number.isFinite( event.clientY ) ? event.clientY : numberRect.bottom,
      );
    };
    const hideTooltip = () => {
      tooltip?.remove();
      tooltip = null;
    };

    number.setAttribute( "aria-label", label );
    number.setAttribute( "aria-describedby", tooltipId );
    number.setAttribute( "tabindex", "0" );
    number.style.cursor = "help";
    number.addEventListener( "mouseenter", showTooltip );
    number.addEventListener( "mousemove", ( event ) => {
      positionTooltip( event.clientX, event.clientY );
    } );
    number.addEventListener( "mouseleave", hideTooltip );
    number.addEventListener( "focus", showTooltip );
    number.addEventListener( "blur", hideTooltip );
  } );
}

function wireRunTestsButtons( container ) {
  container.querySelectorAll( "button[data-run-test-suite]" ).forEach( ( button ) => {
    if ( button.dataset.runTestsWired === "true" ) return;
    button.dataset.runTestsWired = "true";

    const status = button.parentElement.querySelector( ".run-tests-status" );
    const idleLabel = button.textContent;
    button.addEventListener( "click", async () => {
      const apiPath = button.dataset.apiPath;
      button.disabled = true;
      button.textContent = "Opening Windows Terminal\u2026";
      if ( status ) {
        status.hidden = false;
        status.textContent = "Starting the Ubuntu-26.04 test terminal\u2026";
      }

      try {
        const response = await fetch( apiPath, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: "{}",
        } );
        const result = await response.json();
        if ( !response.ok || !result.ok ) {
          throw new Error( result.error || `server returned ${response.status}` );
        }
        button.textContent = `${idleLabel} Again`;
        if ( status ) {
          status.textContent = "Windows Terminal opened with Ubuntu-26.04. It will remain open after the checks finish.";
        }
      } catch ( error ) {
        button.textContent = idleLabel;
        if ( status ) status.textContent = `Could not open Windows Terminal: ${error.message}`;
      } finally {
        button.disabled = false;
      }
    } );
  } );
}

function wireProjectTerminalButtons( container ) {
  container.querySelectorAll( "button[data-open-project-terminal]" ).forEach( ( button ) => {
    if ( button.dataset.projectTerminalWired === "true" ) return;
    button.dataset.projectTerminalWired = "true";

    const status = button.parentElement.querySelector( ".project-terminal-status" );
    const idleLabel = button.textContent;
    button.addEventListener( "click", async () => {
      button.disabled = true;
      button.textContent = "Opening Terminal…";
      if ( status ) {
        status.hidden = false;
        status.textContent = "Opening Windows Terminal in the Interface File project…";
      }

      try {
        const response = await fetch( button.dataset.apiPath, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: "{}",
        } );
        const result = await response.json();
        if ( !response.ok || !result.ok ) {
          throw new Error( result.error || `server returned ${response.status}` );
        }
        button.textContent = idleLabel;
        if ( status ) status.textContent = "Windows Terminal opened in this project's directory.";
      } catch ( error ) {
        button.textContent = idleLabel;
        if ( status ) status.textContent = `Could not open Windows Terminal: ${error.message}`;
      } finally {
        button.disabled = false;
      }
    } );
  } );
}

function rebaseCanonicalLessonUrls( article, sourceUrl ) {
  [
    [ "[href]", "href" ],
    [ "[src]", "src" ],
    [ "[data-api-path]", "data-api-path" ],
  ].forEach( ( [ selector, attribute ] ) => {
    article.querySelectorAll( selector ).forEach( ( element ) => {
      const value = element.getAttribute( attribute );
      if ( value ) element.setAttribute( attribute, new URL( value, sourceUrl ).href );
    } );
  } );
}

async function renderCanonicalConstructionLesson( focus, sourcePath ) {
  focus.innerHTML = '<p class="placeholder">Loading the canonical lesson…</p>';
  try {
    const sourceUrl = new URL( sourcePath, document.baseURI );
    const response = await fetch( sourceUrl, { cache: "no-store" } );
    if ( !response.ok ) throw new Error( `server returned ${response.status}` );

    const sourceDocument = new DOMParser().parseFromString( await response.text(), "text/html" );
    const sourceArticle = sourceDocument.querySelector( "article.construction-lesson" );
    if ( !sourceArticle ) throw new Error( "canonical lesson article is missing" );

    const article = document.importNode( sourceArticle, true );
    rebaseCanonicalLessonUrls( article, sourceUrl );
    focus.replaceChildren( article );
    executeScripts( focus );
    renderLessonDiagrams( focus );
    wireRunTestsButtons( focus );
    wireProjectTerminalButtons( focus );
  } catch ( error ) {
    const message = document.createElement( "p" );
    message.className = "placeholder";
    message.textContent = `Could not load the canonical lesson: ${error.message}`;
    focus.replaceChildren( message );
  }
}

function renderLessonDiagrams( container ) {
  const figures = Array.from( container.querySelectorAll( ".mermaid-figure" ) );
  if ( figures.length === 0 ) return;
  ensureMermaidLoaded().then( async () => {
    window.mermaid.initialize( MERMAID_THEME );
    for ( const figure of figures ) {
      const source = figure.querySelector( "pre.mermaid" );
      if ( !source || source.dataset.rendered === "1" ) continue;
      const definition = source.innerHTML
        .replace( /&lt;/g, "<" ).replace( /&gt;/g, ">" ).replace( /&quot;/g, '"' )
        .replace( /&#39;/g, "'" ).replace( /&nbsp;/g, " " ).replace( /&amp;/g, "&" );
      // Unique per render: the same page can be reopened many times per session.
      const id = "mmd-" + figure.dataset.diagram + "-" + Date.now() + "-" + Math.floor( Math.random() * 1e6 );
      const { svg } = await window.mermaid.render( id, definition );
      figure.querySelector( ".mermaid-canvas" ).innerHTML = svg;
      source.dataset.rendered = "1";
      wireSequenceStepTooltips( figure );
      wireDiagramPanZoom( figure );
    }
  } ).catch( ( error ) => {
    figures.forEach( ( figure ) => {
      figure.querySelector( ".mermaid-canvas" ).textContent = "Diagram could not be rendered: " + error.message;
    } );
  } );
}

async function loadFile( path ) {
  try {
    // Documentation changes frequently while the SPA remains open. Always
    // revalidate the fragment so revisiting an object cannot show stale copy.
    const response = await fetch( path, { cache: "no-store" } );
    if ( !response.ok ) {
      content.innerHTML = `<p class="placeholder">Not built yet (${response.status}): ${path}</p>`;
      return;
    }
    const html = await response.text();
    content.innerHTML = html.trim()
      ? html
      : `<p class="placeholder">This page is still blank — content coming later.</p>`;
    executeScripts( content );
  } catch ( error ) {
    content.innerHTML = `<p class="placeholder">Error loading ${path}: ${error.message}</p>`;
  }
}

function addLink( { text, active, className, onClick } ) {
  const a = document.createElement( "a" );
  a.href = "#";
  a.textContent = text;
  if ( active ) a.classList.add( "active" );
  if ( className ) a.classList.add( className );
  a.addEventListener( "click", ( e ) => {
    e.preventDefault();
    onClick();
  } );
  nav.appendChild( a );
}

// Resolves the node at itemPath under the given section (empty path = the
// section root itself, treated as a folder node).
function resolveNode( top, path ) {
  let node = { label: sections[ top ].label, items: sections[ top ].items };
  path.forEach( ( key ) => {
    node = node.items[ key ];
  } );
  return node;
}

function filePath( file ) {
  return [ currentTop, ...itemPath, file ].join( "/" );
}

function hasChildren( node ) {
  return !!node.items && Object.keys( node.items ).length > 0;
}

function directConstructionTasks( parent ) {
  const list = Array.from( parent.children ).find( ( child ) =>
    child.classList?.contains( "construction-task-tree" )
      || child.classList?.contains( "construction-task-children" )
  );
  return list
    ? Array.from( list.children ).filter( ( child ) => child.matches( "li[data-task-id]" ) )
    : [];
}

function resolveConstructionTask( path ) {
  let parent = content;
  for ( const taskId of path ) {
    const task = directConstructionTasks( parent ).find(
      ( candidate ) => candidate.dataset.taskId === taskId
    );
    if ( !task ) return null;
    parent = task;
  }
  return parent;
}

function constructionTaskRow( task ) {
  return Array.from( task.children ).find( ( child ) =>
    child.classList?.contains( "construction-task-row" )
  ) || null;
}

function constructionTaskTitle( task ) {
  return constructionTaskRow( task )?.querySelector( "strong" )?.textContent.trim()
    || task.dataset.taskLabel
    || task.dataset.taskId;
}

function constructionTaskDescription( task ) {
  return constructionTaskRow( task )?.querySelector( "small" )?.textContent.trim()
    || "This task does not yet have a plain-language description.";
}

function constructionTaskLesson( task ) {
  return Array.from( task.children ).find( ( child ) =>
    child.classList?.contains( "construction-task-lesson" )
  ) || null;
}

function constructionTaskAncestors( task ) {
  const chain = [];
  let parent = task.parentElement?.closest( "li[data-task-id]" ) || null;
  while ( parent ) {
    chain.unshift( parent );
    parent = parent.parentElement?.closest( "li[data-task-id]" ) || null;
  }
  return chain;
}

function constructionTaskSiblings( task ) {
  const parent = task.parentElement?.closest( "li[data-task-id]" ) || content;
  const siblings = directConstructionTasks( parent );
  return siblings.length > 0 ? siblings : [ task ];
}

function constructionStatusWord( status ) {
  return {
    planned: "Not started",
    current: "In progress",
    done: "Done",
  }[ status ] || status;
}

function constructionTrailSentence( task ) {
  return [ ...constructionTaskAncestors( task ), task ]
    .map( ( node, depth ) => {
      const siblings = constructionTaskSiblings( node );
      const unit = depth === 0 ? "Stage" : "Step";
      const position = siblings.indexOf( node ) + 1;
      const title = constructionTaskTitle( node )
        .replace( /^(Stage|Step)\s+\d+\s*[\u2014\u2013-]\s*/, "" );
      return `${unit} ${position} of ${siblings.length} \u2014 ${title}`;
    } )
    .join( "  \u203a  " );
}

function constructionStatusCounts( tasks ) {
  const counts = { total: tasks.length, planned: 0, current: 0, done: 0 };
  tasks.forEach( ( task ) => {
    const status = task.dataset.taskStatus || "planned";
    if ( Object.hasOwn( counts, status ) ) counts[ status ] += 1;
  } );
  return counts;
}

function renderConstructionSummary() {
  const tree = content.querySelector( ".construction-task-tree" );
  if ( !tree ) return;

  const counts = constructionStatusCounts(
    Array.from( tree.querySelectorAll( "li[data-task-id]" ) )
  );
  content.querySelectorAll( "[data-construction-count]" ).forEach( ( target ) => {
    const key = target.dataset.constructionCount;
    target.textContent = String( counts[ key ] ?? 0 );
  } );

  const completion = counts.total === 0
    ? 0
    : Math.round( counts.done / counts.total * 100 );
  content.querySelectorAll( "[data-construction-completion]" ).forEach( ( target ) => {
    target.textContent = `${completion}%`;
  } );
  content.querySelectorAll( "[data-construction-progress]" ).forEach( ( target ) => {
    target.style.width = `${completion}%`;
  } );
}

function appendConstructionLessonSection( parent, heading, body ) {
  const section = document.createElement( "section" );
  const title = document.createElement( "h2" );
  title.textContent = heading;
  section.appendChild( title );

  const paragraph = document.createElement( "p" );
  paragraph.textContent = body;
  section.appendChild( paragraph );
  parent.appendChild( section );
}

function appendConstructionChildRoster( parent, children ) {
  const wrap = document.createElement( "div" );
  wrap.className = "construction-status-table-wrap";
  const table = document.createElement( "table" );
  table.className = "construction-status-table";

  const head = document.createElement( "thead" );
  const headRow = document.createElement( "tr" );
  [ "Step", "Work item", "Status", "What it must deliver" ].forEach( ( text ) => {
    const cell = document.createElement( "th" );
    cell.textContent = text;
    headRow.appendChild( cell );
  } );
  head.appendChild( headRow );

  const bodyRows = document.createElement( "tbody" );
  children.forEach( ( child, index ) => {
    const status = child.dataset.taskStatus || "planned";
    const row = document.createElement( "tr" );

    const position = document.createElement( "td" );
    position.textContent = String( index + 1 );

    const title = document.createElement( "td" );
    title.textContent = constructionTaskTitle( child );

    const statusCell = document.createElement( "td" );
    const chip = document.createElement( "span" );
    chip.className = `construction-status-chip status-${status}`;
    chip.textContent = constructionStatusWord( status );
    statusCell.appendChild( chip );

    const detail = document.createElement( "td" );
    detail.textContent = constructionTaskDescription( child );

    row.append( position, title, statusCell, detail );
    bodyRows.appendChild( row );
  } );

  table.append( head, bodyRows );
  wrap.appendChild( table );
  parent.appendChild( wrap );
}

function numberConstructionLessonChapters( body ) {
  body.querySelectorAll( "h2" ).forEach( ( heading, index ) => {
    heading.textContent = `${index + 1}. ${heading.textContent}`;
  } );
}

function appendConstructionLessonCounts( parent, counts ) {
  const snapshot = document.createElement( "section" );
  const snapshotTitle = document.createElement( "h2" );
  snapshotTitle.textContent = "Progress count for this stage";
  const metrics = document.createElement( "div" );
  metrics.className = "construction-lesson-metrics";
  [
    [ "Items in scope", counts.total ],
    [ "In progress", counts.current ],
    [ "Not started", counts.planned ],
    [ "Done", counts.done ],
  ].forEach( ( [ labelText, value ] ) => {
    const metric = document.createElement( "div" );
    const number = document.createElement( "strong" );
    number.textContent = String( value );
    const label = document.createElement( "span" );
    label.textContent = labelText;
    metric.append( number, label );
    metrics.appendChild( metric );
  } );
  const snapshotCaption = document.createElement( "p" );
  snapshotCaption.className = "construction-progress-caption";
  snapshotCaption.textContent = "Counted from this stage and everything nested underneath it.";
  snapshot.append( snapshotTitle, metrics, snapshotCaption );
  parent.appendChild( snapshot );
}

function renderConstructionTextbookTask( focus, task ) {
  const status = task.dataset.taskStatus || "planned";
  const children = directConstructionTasks( task );
  const scopedTasks = [ task, ...Array.from( task.querySelectorAll( "li[data-task-id]" ) ) ];
  const counts = constructionStatusCounts( scopedTasks );
  const lesson = constructionTaskLesson( task );
  if ( lesson?.dataset.lessonSrc ) {
    renderCanonicalConstructionLesson( focus, lesson.dataset.lessonSrc );
    return;
  }

  const article = document.createElement( "article" );
  article.className = "construction-lesson";

  const masthead = document.createElement( "header" );
  masthead.className = "construction-lesson-masthead";
  const kicker = document.createElement( "p" );
  kicker.className = "construction-lesson-kicker";
  kicker.textContent = "Voice Communication University \u2022 Construction Studio";
  const badge = document.createElement( "span" );
  badge.className = `construction-lesson-badge status-${status}`;
  badge.textContent = constructionStatusWord( status );
  const title = document.createElement( "h1" );
  // A lesson may name its own document title; the row's <strong> is the fallback.
  title.textContent = lesson?.dataset.lessonTitle
    || constructionTaskTitle( task );
  const sourcePath = document.createElement( "button" );
  sourcePath.type = "button";
  sourcePath.className = "construction-lesson-path";
  const indexPath = `${DOC_ROOT}/index.html`;
  sourcePath.textContent = indexPath;
  sourcePath.title = "Click to copy this path";
  sourcePath.setAttribute( "aria-label", `Copy path ${indexPath} to clipboard` );
  sourcePath.addEventListener( "click", () => {
    copyTextToClipboard( indexPath, () => {
      sourcePath.textContent = "Copied to clipboard";
      setTimeout( () => { sourcePath.textContent = indexPath; }, 1500 );
    } );
  } );
  const edition = document.createElement( "p" );
  edition.className = "construction-lesson-edition";
  const objectName = focus.dataset.constructionObject
    || content.querySelector( ".construction-task-tree" )?.dataset.constructionObject
    || "Agent Block";
  edition.textContent = `${objectName} construction lesson`;
  const trail = document.createElement( "p" );
  trail.className = "construction-lesson-trail";
  trail.textContent = constructionTrailSentence( task );
  masthead.append( kicker, badge, title, sourcePath, edition, trail );
  article.appendChild( masthead );

  const body = document.createElement( "div" );
  body.className = "construction-lesson-body";

  const keyIdea = document.createElement( "blockquote" );
  keyIdea.className = "construction-lesson-callout";
  const keyLabel = document.createElement( "span" );
  keyLabel.className = "construction-lesson-callout-label";
  keyLabel.textContent = "What this step is for";
  const purpose = document.createElement( "strong" );
  purpose.textContent = constructionTaskDescription( task );
  keyIdea.append( keyLabel, purpose );
  body.appendChild( keyIdea );

  if ( lesson ) {
    Array.from( lesson.children ).forEach( ( section ) => {
      body.appendChild( section.cloneNode( true ) );
    } );
  }

  if ( children.length > 0 ) {
    const roster = document.createElement( "section" );
    const rosterTitle = document.createElement( "h2" );
    rosterTitle.textContent = "The work inside this stage";
    const rosterIntro = document.createElement( "p" );
    const doneChildren = children.filter( ( child ) => child.dataset.taskStatus === "done" ).length;
    const nextChild = children.find( ( child ) => child.dataset.taskStatus !== "done" );
    rosterIntro.textContent = nextChild
      ? `${doneChildren} of ${children.length} finished. The next one to work on is \u201c${constructionTaskTitle( nextChild )}\u201d.`
      : `All ${children.length} are finished, so this stage is only waiting on its own sign-off.`;
    roster.append( rosterTitle, rosterIntro );
    body.appendChild( roster );
    appendConstructionChildRoster( body, children );
  }

  if ( children.length > 0 ) appendConstructionLessonCounts( body, counts );
  const hasAuthoredContinuation = Array.from(
    body.querySelectorAll( ":scope > section > h2" ),
  ).some( ( heading ) => heading.textContent.trim() === "How to continue from here" );
  if ( !hasAuthoredContinuation ) {
    const continuation = children.length > 0
      ? `This is a stage, not a single job: it opens into ${children.length} smaller ${children.length === 1 ? "item" : "items"}. Open the red-tagged sidebar entries one at a time to read each one; the rest of the tree stays hidden.`
      : "This is a leaf: the smallest unit in this plan, small enough that one person can finish it and prove it in one sitting. Nothing under it is hidden.";
    appendConstructionLessonSection( body, "How to continue from here", continuation );
  }

  numberConstructionLessonChapters( body );
  article.appendChild( body );

  const footer = document.createElement( "footer" );
  footer.className = "construction-lesson-footer";
  footer.textContent = `Voice Communication University \u2022 ${objectName} Construction Status`;
  article.appendChild( footer );
  focus.appendChild( article );
  // Cloned <script> nodes never run on their own, so re-create any the lesson
  // carries the same way loadFile() does -- and only after the article is in
  // the document. Mermaid figures are rendered here rather than by a script in
  // every lesson page.
  executeScripts( focus );
  renderLessonDiagrams( focus );
  wireRunTestsButtons( focus );
}

function renderConstructionTaskContent( task ) {
  const focus = content.querySelector( ".construction-task-focus" );
  if ( !focus ) return;

  const intro = content.querySelector( ".construction-plan-intro" );
  if ( intro ) intro.hidden = Boolean( task );

  focus.replaceChildren();
  if ( !task ) {
    focus.hidden = true;
    delete focus.dataset.taskStatus;
    return;
  }

  focus.hidden = false;
  focus.dataset.taskStatus = task.dataset.taskStatus || "planned";

  if ( focus.classList.contains( "construction-textbook-focus" ) ) {
    renderConstructionTextbookTask( focus, task );
    return;
  }

  const label = document.createElement( "p" );
  label.className = "construction-task-focus-label";
  label.textContent = "Selected task";
  focus.appendChild( label );

  const row = constructionTaskRow( task );
  if ( row ) focus.appendChild( row.cloneNode( true ) );

  if ( directConstructionTasks( task ).length > 0 ) {
    const hint = document.createElement( "p" );
    hint.className = "placeholder construction-task-focus-hint";
    hint.textContent = "Choose a child task in the sidebar, or use Back to collapse this task.";
    focus.appendChild( hint );
  }
}

function selectConstructionTask( task ) {
  focusedConstructionTask = task.dataset.taskId;
  renderConstructionTaskContent( task );
  if ( directConstructionTasks( task ).length > 0 ) {
    constructionTaskPath.push( task.dataset.taskId );
  }
  renderNav();
}

function goUpConstructionTasks() {
  if ( constructionTaskPath.length === 0 ) {
    constructionTaskPath = null;
    focusedConstructionTask = null;
    renderConstructionTaskContent( null );
    renderNav();
    return;
  }

  focusedConstructionTask = constructionTaskPath.pop();
  renderConstructionTaskContent( resolveConstructionTask( [
    ...constructionTaskPath,
    focusedConstructionTask,
  ] ) );
  renderNav();
}

function renderConstructionTaskNav() {
  const parent = resolveConstructionTask( constructionTaskPath );
  if ( !parent ) {
    constructionTaskPath = null;
    renderNav();
    return;
  }

  addLink( { text: "Back", className: "back", onClick: () => goUpConstructionTasks() } );
  directConstructionTasks( parent ).forEach( ( task ) => {
    const children = directConstructionTasks( task );
    addLink( {
      text: task.dataset.taskLabel || task.dataset.taskId,
      active: task.dataset.taskId === focusedConstructionTask,
      className: children.length > 0 ? "has-children" : undefined,
      onClick: () => selectConstructionTask( task ),
    } );
  } );
}

function resetConstructionTaskNav() {
  constructionTaskPath = null;
  focusedConstructionTask = null;
}

function renderNav() {
  nav.innerHTML = "";

  if ( currentTop === "home" ) {
    addLink( { text: "Home", active: true, onClick: () => goHome() } );
    Object.keys( sections ).forEach( ( key ) => {
      addLink( {
        text: sections[ key ].label,
        className: hasChildren( sections[ key ]) ? "has-children" : undefined,
        onClick: () => selectSection( key ),
      } );
    } );
    return;
  }

  const node = resolveNode( currentTop, itemPath );

  if ( node.items ) {
    // Folder level (section root or a nested group like "adapters").
    if ( itemPath.length === 0 ) {
      addLink( { text: "Home", onClick: () => goHome() } );
    } else {
      addLink( { text: "Back", className: "back", onClick: () => goUp() } );
    }
    Object.keys( node.items ).forEach( ( key ) => {
      const child = node.items[ key ];
      addLink( {
        text: child.label,
        className: hasChildren( child ) ? "has-children" : undefined,
        onClick: () => selectNode( key ),
      } );
    } );
    return;
  }

  if (
    currentDetail === "status"
    && constructionTaskPath !== null
    && content.querySelector( ".construction-task-tree" )
  ) {
    renderConstructionTaskNav();
    return;
  }

  // Leaf item: fixed detail tabs.
  addLink( { text: "Back", className: "back", onClick: () => goUp() } );
  detailTabs.forEach( ( tab ) => {
    addLink( {
      text: tab.label,
      active: tab.key === currentDetail,
      onClick: () => selectDetail( tab ),
    } );
  } );
}

// Folder-level overview: "<top>/.../_overview.html", sibling of the folder's
// item subfolders. Optional -- a missing/empty one just falls back to the
// old "Choose an item above." placeholder, so existing folders with no
// overview yet keep working unchanged.
async function showCurrentContent() {
  const node = resolveNode( currentTop, itemPath );
  if ( !node.items ) {
    loadFile( filePath( overviewFile ) );
    return;
  }

  const heading = `<h1>${node.label}</h1>`;
  if ( Object.keys( node.items ).length === 0 ) {
    content.innerHTML = `${heading}<p class="placeholder">No items built yet — coming later.</p>`;
    return;
  }

  const chooseMsg = `${heading}<p class="placeholder">Choose an item above.</p>`;
  try {
    const response = await fetch(
      [ currentTop, ...itemPath, "_overview.html" ].join( "/" ),
      { cache: "no-store" },
    );
    if ( response.ok ) {
      const html = await response.text();
      content.innerHTML = html.trim() ? `${heading}${html}` : chooseMsg;
      executeScripts( content );
      return;
    }
  } catch ( error ) {
    // fall through to the placeholder below
  }
  content.innerHTML = chooseMsg;
}

function goHome() {
  currentTop = "home";
  itemPath = [];
  currentDetail = null;
  resetConstructionTaskNav();
  renderNav();
  loadFile( "home_claude_md.html" );
}

function selectSection( top ) {
  currentTop = top;
  itemPath = [];
  currentDetail = null;
  resetConstructionTaskNav();
  renderNav();
  showCurrentContent();
}

function selectNode( key ) {
  itemPath.push( key );
  currentDetail = null;
  resetConstructionTaskNav();
  renderNav();
  showCurrentContent();
}

function goUp() {
  if ( itemPath.length === 0 ) {
    goHome();
    return;
  }
  itemPath.pop();
  currentDetail = null;
  resetConstructionTaskNav();
  renderNav();
  showCurrentContent();
}

function itemKey() {
  return [ currentTop, ...itemPath ].join( "/" );
}

// Runs once per detail-tab page load, not on a timer -- fetches
// server.py's /api/git-status route for the item currently open and
// updates the "Update Documentation" nav link + button accordingly.
async function refreshGitStatus() {
  const key = itemKey();
  try {
    // Relative, not "/api/..." -- this page can be loaded at the server's
    // own root or under a reverse-proxy subpath (e.g. /agent-block/ in the
    // letta-code dashboard), and an absolute path would resolve to the
    // proxy's domain root instead of back to this same server.
    const response = await fetch( `api/git-status?item=${encodeURIComponent( key )}` );
    gitStatusCache[ key ] = response.ok ? await response.json() : { exists: false, dirty: false };
  } catch ( error ) {
    gitStatusCache[ key ] = { exists: false, dirty: false };
  }
  applyGitStatusUI();
}

function applyGitStatusUI() {
  const status = gitStatusCache[ itemKey() ];
  const updateLink = Array.from( nav.querySelectorAll( "a" ) ).find(
    ( a ) => a.textContent === "Update Documentation"
  );
  if ( updateLink ) {
    updateLink.classList.toggle( "tab-dirty", !!( status && status.dirty ) );
  }
  if ( currentDetail === "update_docs" ) {
    renderUpdateButton( status );
  }
}

function renderUpdateButton( status ) {
  // .update-doc-log (if a run is/was in progress) lives inside the bar, so
  // removing the bar takes it with it.
  content.querySelector( ".update-doc-bar" )?.remove();

  const bar = document.createElement( "div" );
  bar.className = "update-doc-bar";

  const btn = document.createElement( "button" );
  btn.type = "button";
  btn.className = "update-doc-btn";
  btn.disabled = true;
  if ( !status ) {
    btn.textContent = "Checking git status…";
  } else if ( status.dirty ) {
    btn.disabled = false;
    btn.textContent = "Update Documentation";
  } else {
    btn.textContent = status.exists ? "Up to date" : "Source not found";
  }
  bar.appendChild( btn );

  const hint = document.createElement( "p" );
  hint.className = "placeholder";
  hint.textContent = status && status.dirty
    ? ( status.docs_missing ? "Docs not generated yet." : "Source changed since docs were generated." )
    : "No uncommitted source changes detected.";
  bar.appendChild( hint );

  content.prepend( bar );

  if ( status && status.dirty ) {
    btn.addEventListener( "click", () => startUpdateRun( btn, hint ) );
  }
}

// Click -> POST api/run-update starts update_documentation_agent.py in the
// background on the server (see server.py) -> poll api/run-update-status
// every few seconds, streaming its log tail into the hint line, until it
// exits -> reload the report and re-check git status.
function startUpdateRun( btn, hint ) {
  const item = itemKey();
  btn.disabled = true;
  btn.textContent = "Starting…";
  hint.textContent = "";

  // Sibling of hint inside .update-doc-bar, not content.insertBefore(...,
  // hint.nextSibling) -- hint's parent is bar, not content, so that would
  // throw NotFoundError (hint isn't a direct child of content).
  const logBox = document.createElement( "pre" );
  logBox.className = "update-doc-log";
  hint.after( logBox );

  fetch( "api/run-update", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify( { item } ),
  } )
    .then( ( r ) => r.json() )
    .then( ( data ) => {
      if ( !data.ok ) {
        btn.disabled = false;
        btn.textContent = "Update Documentation";
        logBox.textContent = `Could not start: ${data.error || "unknown error"}`;
        return;
      }
      btn.textContent = "Running…";
      pollUpdateRun( item, btn, logBox );
    } )
    .catch( ( error ) => {
      btn.disabled = false;
      btn.textContent = "Update Documentation";
      logBox.textContent = `Request failed: ${error.message}`;
    } );
}

function pollUpdateRun( item, btn, logBox ) {
  fetch( `api/run-update-status?item=${encodeURIComponent( item )}` )
    .then( ( r ) => r.json() )
    .then( ( data ) => {
      logBox.textContent = data.log_tail || "(starting…)";
      logBox.scrollTop = logBox.scrollHeight;

      if ( data.running ) {
        setTimeout( () => pollUpdateRun( item, btn, logBox ), 3000 );
        return;
      }

      btn.textContent = data.exit_code === 0 ? "Done" : `Exited (code ${data.exit_code})`;
      // Only reload if the user hasn't navigated elsewhere meanwhile.
      if ( currentDetail === "update_docs" && itemKey() === item ) {
        const tab = detailTabs.find( ( t ) => t.key === "update_docs" );
        loadFile( filePath( tab.file ) ).then( () => refreshGitStatus() );
      }
    } )
    .catch( () => setTimeout( () => pollUpdateRun( item, btn, logBox ), 3000 ) );
}

function selectDetail( tab ) {
  currentDetail = tab.key;
  resetConstructionTaskNav();
  renderNav();
  const selectedItem = itemKey();
  return loadFile( filePath( tab.file ) ).then( () => {
    if ( currentDetail !== tab.key || itemKey() !== selectedItem ) return;
    if ( tab.key === "status" && content.querySelector( ".construction-task-tree" ) ) {
      constructionTaskPath = [];
      renderConstructionSummary();
      renderConstructionTaskContent( null );
      renderNav();
    }
    refreshGitStatus();
  } );
}

// --- URL hash deep-linking --------------------------------------------------
// Normal in-app navigation has no history integration (see CLAUDE.md) -- nav
// state lives entirely in the module-level variables above. This is the one
// exception: a hand-written link in a generated doc page (e.g. a lesson's
// "see the plan's next-steps queue" link) needs to land a reader on a real
// tab with the real sidebar, not on a bare fragment file that was only ever
// meant to be fetched into #content. Read once on load; the app itself never
// writes to location.hash, so there's nothing to keep in sync afterward.
// Format: #item=<top>/<path>/<to>/<leaf>&tab=<detailTabs key>&anchor=<id>
function parseHashRoute() {
  const raw = window.location.hash.replace( /^#/, "" );
  if ( !raw ) return null;
  const params = new URLSearchParams( raw );
  const item = params.get( "item" );
  if ( !item ) return null;
  return {
    pathSegments: item.split( "/" ).filter( Boolean ),
    tab: params.get( "tab" ),
    anchor: params.get( "anchor" ),
  };
}

function applyHashRoute( route ) {
  const [ top, ...path ] = route.pathSegments;
  if ( !sections[ top ] ) return false;

  let node = { items: sections[ top ].items };
  for ( const key of path ) {
    if ( !node.items || !node.items[ key ] ) return false;
    node = node.items[ key ];
  }

  currentTop = top;
  itemPath = path;
  resetConstructionTaskNav();

  const scrollToAnchor = () => {
    if ( !route.anchor ) return;
    content.querySelector( `#${CSS.escape( route.anchor )}` )?.scrollIntoView();
  };

  const tab = detailTabs.find( ( t ) => t.key === route.tab );
  if ( tab ) {
    selectDetail( tab ).then( scrollToAnchor );
  } else {
    currentDetail = null;
    renderNav();
    showCurrentContent().then( scrollToAnchor );
  }
  return true;
}

const hashRoute = parseHashRoute();
if ( !hashRoute || !applyHashRoute( hashRoute ) ) {
  goHome();
}

// A route link clicked while already inside the app (e.g. the leaf lesson is
// open via loadFile(), not a fresh navigation) only changes location.hash --
// no page load happens, so the read-once block above never reruns. Handle
// that same-document case explicitly.
window.addEventListener( "hashchange", () => {
  const route = parseHashRoute();
  if ( route ) applyHashRoute( route );
} );
