"use client";

import { Fragment, useEffect, useMemo, useState } from "react";

type JsonObject = Record<string, unknown>;

type PlateLayoutRow = {
  [key: string]: unknown;
  well: string;
  label: string;
  sample?: string;
  condition?: string;
  replicate?: number;
  assay_type?: string;
};

type WellContentRow = {
  [key: string]: unknown;
  well: string;
  label: string;
  liquid_name: string;
  liquid_role: string;
  volume_ul: number;
  source_labware: string;
  source_well: string;
};

type SourceMapRow = {
  [key: string]: unknown;
  liquid_name: string;
  liquid_role: string;
  source_labware: string;
  source_well: string;
  available_volume_ul: number;
  required_volume_ul: number;
  status: string;
};

type ValidationRow = {
  [key: string]: unknown;
  check_name: string;
  status: string;
  detail: string;
  severity: string;
};

type ExperimentState = {
  plate_layout?: PlateLayoutRow[];
  well_contents?: WellContentRow[];
  source_map?: SourceMapRow[];
  validation_report?: ValidationRow[];
  transfer_table?: JsonObject[];
  summary_matrix?: JsonObject[];
  pending_operations?: JsonObject[];
  pending_diff?: JsonObject[];
  correction_chat?: { role: string; message: string }[];
  passed?: boolean;
  [key: string]: unknown;
};

type ExperimentResponse = {
  experiment_id: string;
  state: ExperimentState;
};

type DataTab = "validation" | "contents" | "sources" | "transfers" | "diff";
type LegendItem = {
  className: string;
  label: string;
};

type ManualWellEdit = {
  original_well: string;
  well: string;
  label: string;
  sample: string;
  condition: string;
  replicate: number;
  assay_type: string;
};

type ManualSourceEdit = {
  liquid_name: string;
  liquid_role: string;
  original_source_labware: string;
  original_source_well: string;
  source_labware: string;
  source_well: string;
  available_volume_ul: number;
};

type ManualVolumeUpdate = {
  well: string;
  liquid_name: string;
  liquid_role: string;
  source_labware: string;
  source_well: string;
  volume_ul: number;
};

const apiBaseUrl = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://127.0.0.1:8000";
const defaultProvider = process.env.NEXT_PUBLIC_LLM_PROVIDER ?? "Mock";
const defaultModel = process.env.NEXT_PUBLIC_LLM_MODEL ?? "mock";
const rows = ["A", "B", "C", "D", "E", "F", "G", "H"];
const columns = Array.from({ length: 12 }, (_, index) => String(index + 1));

const exampleProtocol = `There are 12 templates T1-T12 and 4 primer pairs P1-P4.
T1-T6 should use P1 and P2. T7-T12 should use P1-P4.
Each reaction is 20 uL: enzyme 9 uL, water 5 uL, forward primer 1 uL, reverse primer 1 uL, template 4 uL.
Enzyme is in Reservoir_1 A1. Water is in Reservoir_1 A2.
P1 forward/reverse are in Reservoir_1 A3/A4.
P2 forward/reverse are in Reservoir_1 B3/B4.
P3 forward/reverse are in Reservoir_1 C3/C4.
P4 forward/reverse are in Reservoir_1 D3/D4.
Templates are in Sample_plate A1-A12.`;

function wellAddress(row: string, column: string): string {
  return `${row}${column}`;
}

function formatVolume(value: number | undefined): string {
  if (value === undefined || Number.isNaN(value)) {
    return "?";
  }
  return Number.isInteger(value) ? String(value) : value.toFixed(2);
}

function hashToBucket(value: string, bucketCount: number): number {
  let hash = 0;
  for (let index = 0; index < value.length; index += 1) {
    hash = (hash * 31 + value.charCodeAt(index)) % bucketCount;
  }
  return hash;
}

function targetRenderClass(row: PlateLayoutRow | undefined): string {
  if (!row) {
    return "";
  }
  return `target-render-${hashToBucket(targetRenderKey(row), 8)}`;
}

function sourceRenderClass(row: SourceMapRow | undefined): string {
  if (!row) {
    return "";
  }
  return `source-render-${hashToBucket(sourceRenderKey(row), 8)}`;
}

function targetRenderKey(row: PlateLayoutRow): string {
  return String(row.condition || row.sample || row.label || row.well);
}

function sourceRenderKey(row: SourceMapRow): string {
  return String(row.liquid_role || row.liquid_name || row.source_labware || "Liquid");
}

function makeLegendItems<T>(
  rows: T[] | undefined,
  getKey: (row: T) => string,
  prefix: "target" | "source",
  fallback: string
): LegendItem[] {
  const seen = new Map<string, LegendItem>();
  for (const row of rows ?? []) {
    const key = getKey(row).trim() || fallback;
    if (!seen.has(key)) {
      seen.set(key, {
        className: `${prefix}-legend-${hashToBucket(key, 8)}`,
        label: key,
      });
    }
  }
  return Array.from(seen.values()).slice(0, 8);
}

async function readApiError(response: Response): Promise<string> {
  try {
    const payload = (await response.json()) as { detail?: string };
    return payload.detail ?? `Request failed: HTTP ${response.status}`;
  } catch {
    return `Request failed: HTTP ${response.status}`;
  }
}

export default function WorkspacePage() {
  const [protocol, setProtocol] = useState("");
  const [provider, setProvider] = useState(defaultProvider);
  const [model, setModel] = useState(defaultModel);
  const [accessCode, setAccessCode] = useState("");
  const [correction, setCorrection] = useState("move A1 to H12");
  const [experiment, setExperiment] = useState<ExperimentResponse | null>(null);
  const [selectedWell, setSelectedWell] = useState("A1");
  const [selectedSourceLabware, setSelectedSourceLabware] = useState("");
  const [selectedSourceIdentity, setSelectedSourceIdentity] = useState<{
    liquid_name: string;
    source_labware: string;
    source_well: string;
  } | null>(null);
  const [activeDataTab, setActiveDataTab] = useState<DataTab>("validation");
  const [error, setError] = useState("");
  const [busyAction, setBusyAction] = useState("");

  useEffect(() => {
    const savedAccessCode = window.sessionStorage.getItem("labmate.demo_access_code") ?? "";
    const savedExperimentId = window.sessionStorage.getItem("labmate.experiment_id");
    const savedProtocol = window.sessionStorage.getItem("labmate.protocol");
    const savedProvider = window.sessionStorage.getItem("labmate.provider");
    const savedModel = window.sessionStorage.getItem("labmate.model");
    setAccessCode(savedAccessCode);
    if (savedProtocol !== null) setProtocol(savedProtocol);
    if (savedProvider) setProvider(savedProvider);
    if (savedModel) setModel(savedModel);
    if (!savedExperimentId) return;

    setBusyAction("restore");
    fetch(`${apiBaseUrl}/api/experiments/${savedExperimentId}`, {
      headers: { "X-Demo-Access-Code": savedAccessCode }
    })
      .then(async (response) => {
        if (!response.ok) throw new Error(await readApiError(response));
        return (await response.json()) as ExperimentResponse;
      })
      .then((response) => {
        setExperiment(response);
        setSelectedWell(response.state.plate_layout?.[0]?.well ?? "A1");
        setSelectedSourceLabware(response.state.source_map?.[0]?.source_labware ?? "");
      })
      .catch((restoreError) => {
        window.sessionStorage.removeItem("labmate.experiment_id");
        setError(restoreError instanceof Error ? `Could not restore experiment: ${restoreError.message}` : "Could not restore experiment.");
      })
      .finally(() => setBusyAction(""));
  }, []);

  const state = experiment?.state;

  const sourceLabwares = useMemo(() => {
    const labwares = new Set<string>();
    state?.source_map?.forEach((row) => {
      if (row.source_labware) {
        labwares.add(row.source_labware);
      }
    });
    return Array.from(labwares).sort();
  }, [state]);

  const wellContentsForSelection = useMemo(
    () => (state?.well_contents ?? []).filter((row) => row.well === selectedWell),
    [state, selectedWell]
  );

  const selectedLayout = useMemo(
    () => (state?.plate_layout ?? []).find((row) => row.well === selectedWell),
    [state, selectedWell]
  );
  const selectedSource = useMemo(
    () =>
      (state?.source_map ?? []).find(
        (row) =>
          row.liquid_name === selectedSourceIdentity?.liquid_name &&
          row.source_labware === selectedSourceIdentity?.source_labware &&
          row.source_well === selectedSourceIdentity?.source_well
      ),
    [state?.source_map, selectedSourceIdentity]
  );
  const selectedSourceTransfers = useMemo(
    () =>
      (state?.well_contents ?? []).filter(
        (row) =>
          row.liquid_name === selectedSource?.liquid_name &&
          row.source_labware === selectedSource?.source_labware &&
          row.source_well === selectedSource?.source_well
      ),
    [state?.well_contents, selectedSource]
  );
  const targetLegendItems = useMemo(
    () => makeLegendItems(state?.plate_layout, targetRenderKey, "target", "Target group"),
    [state?.plate_layout]
  );
  const sourceLegendItems = useMemo(
    () => makeLegendItems(state?.source_map, sourceRenderKey, "source", "Liquid group"),
    [state?.source_map]
  );

  const transferCount = state?.transfer_table?.length ?? 0;
  const targetWellCount = state?.plate_layout?.length ?? 0;
  const liquidCount = new Set((state?.well_contents ?? []).map((row) => row.liquid_name)).size;
  const validationErrors = (state?.validation_report ?? []).filter(
    (row) => row.status !== "PASS" && ["error", "critical"].includes(row.severity)
  ).length;
  const hasPendingCorrection = Boolean(state?.pending_operations?.length);
  const validationLabel = !state ? "Not generated" : validationErrors === 0 ? "Passed" : `${validationErrors} issue(s)`;

  async function postJson<TResponse>(path: string, body: JsonObject): Promise<TResponse> {
    const response = await fetch(`${apiBaseUrl}${path}`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Demo-Access-Code": accessCode.trim()
      },
      body: JSON.stringify(body)
    });
    if (!response.ok) {
      throw new Error(await readApiError(response));
    }
    return (await response.json()) as TResponse;
  }

  function rememberExperiment(response: ExperimentResponse) {
    setExperiment(response);
    window.sessionStorage.setItem("labmate.experiment_id", response.experiment_id);
  }

  async function runAction(action: string, handler: () => Promise<void>) {
    setBusyAction(action);
    setError("");
    try {
      await handler();
    } catch (actionError) {
      setError(actionError instanceof Error ? actionError.message : "Request failed.");
    } finally {
      setBusyAction("");
    }
  }

  async function generateLayout() {
    await runAction("generate", async () => {
      const response = await postJson<ExperimentResponse>("/api/generate-layout", {
        protocol,
        provider,
        model
      });
      rememberExperiment(response);
      setActiveDataTab("validation");
      const firstWell = response.state.plate_layout?.[0]?.well;
      if (firstWell) {
        setSelectedWell(firstWell);
      }
      const firstSourceLabware = response.state.source_map?.[0]?.source_labware;
      if (firstSourceLabware) {
        setSelectedSourceLabware(firstSourceLabware);
      }
      setSelectedSourceIdentity(null);
    });
  }

  async function interpretCorrection() {
    if (!experiment) {
      return;
    }
    await runAction("interpret", async () => {
      const response = await postJson<ExperimentResponse>("/api/interpret-correction", {
        experiment_id: experiment.experiment_id,
        command: correction,
        provider,
        model
      });
      rememberExperiment(response);
      setActiveDataTab("diff");
    });
  }

  async function applyCorrection() {
    if (!experiment) {
      return;
    }
    await runAction("apply", async () => {
      const response = await postJson<ExperimentResponse>("/api/apply-correction", {
        experiment_id: experiment.experiment_id,
        command: correction
      });
      rememberExperiment(response);
      setActiveDataTab("validation");
    });
  }

  async function saveManualWell(edit: ManualWellEdit) {
    if (!experiment) {
      return;
    }
    await runAction("manual-edit", async () => {
      const response = await postJson<ExperimentResponse>("/api/manual-edit-well", {
        experiment_id: experiment.experiment_id,
        ...edit
      });
      rememberExperiment(response);
      setSelectedWell(edit.well.toUpperCase());
      setActiveDataTab("validation");
    });
  }

  async function saveManualSource(edit: ManualSourceEdit) {
    if (!experiment) {
      return;
    }
    await runAction("manual-source", async () => {
      const response = await postJson<ExperimentResponse>("/api/manual-edit-source", {
        experiment_id: experiment.experiment_id,
        ...edit
      });
      rememberExperiment(response);
      setSelectedSourceLabware(edit.source_labware);
      setSelectedSourceIdentity({
        liquid_name: edit.liquid_name,
        source_labware: edit.source_labware,
        source_well: edit.source_well.toUpperCase()
      });
      setActiveDataTab("sources");
    });
  }

  async function saveManualVolumes(updates: ManualVolumeUpdate[]) {
    if (!experiment) {
      return;
    }
    await runAction("manual-volumes", async () => {
      const response = await postJson<ExperimentResponse>("/api/manual-edit-volumes", {
        experiment_id: experiment.experiment_id,
        updates
      });
      rememberExperiment(response);
      setActiveDataTab("validation");
    });
  }

  async function validateExperiment() {
    if (!experiment) {
      return;
    }
    await runAction("validate", async () => {
      const response = await postJson<ExperimentResponse>("/api/validate", {
        experiment_id: experiment.experiment_id
      });
      rememberExperiment(response);
      setActiveDataTab("validation");
    });
  }

  async function exportWorkbook() {
    if (!experiment) {
      return;
    }
    await runAction("export", async () => {
      const response = await fetch(`${apiBaseUrl}/api/export`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-Demo-Access-Code": accessCode.trim()
        },
        body: JSON.stringify({
          experiment_id: experiment.experiment_id,
          filename: "labmate_experiment.xlsx"
        })
      });
      if (!response.ok) {
        throw new Error(await readApiError(response));
      }
      const blob = await response.blob();
      const url = window.URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = "labmate_experiment.xlsx";
      link.click();
      window.URL.revokeObjectURL(url);
    });
  }

  return (
    <main className="app-shell">
      <header className="product-bar">
        <div>
          <div className="brand-row">
            <span className="brand-mark">LabMate</span>
            <span className="module-chip">Pipetting Workspace</span>
          </div>
          <h1>Automated Liquid Handling</h1>
        </div>
        <div className="run-strip" aria-label="Experiment status">
          <div>
            <span>Run</span>
            <strong>{experiment ? experiment.experiment_id.slice(0, 8) : "Draft"}</strong>
          </div>
          <div>
            <span>Validation</span>
            <strong className={validationErrors ? "status-error" : "status-pass"}>{validationLabel}</strong>
          </div>
          <button className="secondary" onClick={validateExperiment} disabled={Boolean(busyAction) || !experiment}>
            {busyAction === "validate" ? "Validating..." : "Validate"}
          </button>
          <button onClick={exportWorkbook} disabled={Boolean(busyAction) || !experiment}>
            {busyAction === "export" ? "Exporting..." : "Export Excel"}
          </button>
        </div>
      </header>

      {error ? <div className="error">{error}</div> : null}

      <section className="pipetting-workbench">
        <aside className="setup-panel">
          <div className="panel-title">
            <h2>Protocol</h2>
            <span>{provider}</span>
          </div>
          <textarea
            id="protocol"
            value={protocol}
            onChange={(event) => {
              setProtocol(event.target.value);
              window.sessionStorage.setItem("labmate.protocol", event.target.value);
            }}
            placeholder="Paste a liquid-handling protocol here."
          />
          <p className="field-note">Example protocol is hidden by default. Paste your experiment instructions here to generate a layout.</p>

          <div className="settings-grid compact">
            <div>
              <label htmlFor="provider">Provider</label>
              <select
                id="provider"
                value={provider}
                onChange={(event) => {
                  const nextProvider = event.target.value;
                  setProvider(nextProvider);
                  window.sessionStorage.setItem("labmate.provider", nextProvider);
                  if (nextProvider === "Mock") {
                    setModel("mock");
                    window.sessionStorage.setItem("labmate.model", "mock");
                  } else if (model === "mock") {
                    setModel("openrouter/free");
                    window.sessionStorage.setItem("labmate.model", "openrouter/free");
                  }
                }}
              >
                <option value="Mock">Mock</option>
                <option value="OpenRouter">OpenRouter</option>
                <option value="OpenAI">OpenAI</option>
              </select>
            </div>
            <div>
              <label htmlFor="model">Model</label>
              <input
                id="model"
                value={model}
                onChange={(event) => {
                  setModel(event.target.value);
                  window.sessionStorage.setItem("labmate.model", event.target.value);
                }}
                disabled={provider === "Mock"}
              />
            </div>
          </div>

          <label htmlFor="access-code">Demo access code</label>
          <input
            id="access-code"
            type="password"
            value={accessCode}
            onChange={(event) => {
              setAccessCode(event.target.value);
              window.sessionStorage.setItem("labmate.demo_access_code", event.target.value);
            }}
            placeholder="Required for the hosted demo"
          />

          <button className="full-width" onClick={generateLayout} disabled={Boolean(busyAction) || !protocol.trim()}>
            {busyAction === "generate" ? "Generating..." : "Generate Layout"}
          </button>

          <div className="correction-block">
            <div className="panel-title">
              <h2>Correction</h2>
              <span>{hasPendingCorrection ? "Pending" : "Idle"}</span>
            </div>
            <input id="correction" value={correction} onChange={(event) => setCorrection(event.target.value)} />
            <div className="button-row">
              <button className="secondary" onClick={interpretCorrection} disabled={Boolean(busyAction) || !experiment || !correction.trim()}>
                {busyAction === "interpret" ? "Interpreting..." : "Interpret"}
              </button>
              <button onClick={applyCorrection} disabled={Boolean(busyAction) || !hasPendingCorrection}>
                {busyAction === "apply" ? "Applying..." : "Apply"}
              </button>
            </div>
          </div>
        </aside>

        <section className="plate-stage">
          <div className="metrics">
            <Metric label="Target wells" value={targetWellCount || "-"} />
            <Metric label="Liquids" value={liquidCount || "-"} />
            <Metric label="Transfers" value={transferCount || "-"} />
            <Metric
              label="Selected"
              value={selectedSource ? `${selectedSource.source_labware} ${selectedSource.source_well}` : selectedWell}
            />
          </div>

          <div className="plate-stack">
            <div className="plate-panel">
              <div className="panel-header">
                <h2>Source Labware</h2>
                <select
                  value={selectedSourceLabware || sourceLabwares[0] || ""}
                  onChange={(event) => setSelectedSourceLabware(event.target.value)}
                  disabled={!sourceLabwares.length}
                >
                  {sourceLabwares.length ? (
                    sourceLabwares.map((labware) => (
                      <option key={labware} value={labware}>
                        {labware}
                      </option>
                    ))
                  ) : (
                    <option value="">No source map</option>
                  )}
                </select>
              </div>
              <PlateGrid
                kind="source"
                sourceMap={state?.source_map ?? []}
                selectedSourceLabware={selectedSourceLabware || sourceLabwares[0] || ""}
                selectedSource={selectedSource}
                onSelectSource={(source) => {
                  setSelectedSourceIdentity({
                    liquid_name: source.liquid_name,
                    source_labware: source.source_labware,
                    source_well: source.source_well
                  });
                }}
              />
              <PlateLegend items={sourceLegendItems} emptyText="Source colors appear after layout generation." />
            </div>

            <div className="plate-panel">
              <div className="panel-header">
                <h2>Destination Plate</h2>
                <span className="muted">96-well target layout</span>
              </div>
              <PlateGrid
                kind="target"
                plateLayout={state?.plate_layout ?? []}
                selectedWell={selectedWell}
                onSelectWell={(well) => {
                  setSelectedWell(well);
                  setSelectedSourceIdentity(null);
                }}
              />
              <PlateLegend items={targetLegendItems} emptyText="Target colors appear after layout generation." />
            </div>
          </div>
        </section>

        <aside className="inspector-panel">
          <div className="panel-title">
            <h2>{selectedSource ? "Source Inspector" : "Well Inspector"}</h2>
            <span>{selectedSource ? `${selectedSource.source_labware} ${selectedSource.source_well}` : selectedWell}</span>
          </div>
          {selectedSource ? (
            <>
              <h3>Source Volume</h3>
              <SourceVolumeSummary source={selectedSource} />
              <h3>Manual Edit</h3>
              <ManualSourceEditor
                key={`${selectedSource.liquid_name}-${selectedSource.source_labware}-${selectedSource.source_well}-${selectedSource.available_volume_ul}`}
                source={selectedSource}
                saving={busyAction === "manual-source"}
                disabled={Boolean(busyAction)}
                onSave={saveManualSource}
              />
              <h3>Target Dispenses</h3>
              <VolumeEditor
                key={selectedSourceTransfers.map((row) => `${row.well}-${row.liquid_name}-${row.volume_ul}`).join("|")}
                rows={selectedSourceTransfers}
                showTarget
                saving={busyAction === "manual-volumes"}
                disabled={Boolean(busyAction)}
                onSave={saveManualVolumes}
              />
            </>
          ) : (
            <>
              <h3>Manual Edit</h3>
              {selectedLayout ? (
                <ManualWellEditor
                  key={`${selectedLayout.well}-${selectedLayout.label}-${selectedLayout.sample}-${selectedLayout.condition}-${selectedLayout.replicate}-${selectedLayout.assay_type}`}
                  row={selectedLayout}
                  saving={busyAction === "manual-edit"}
                  disabled={Boolean(busyAction)}
                  onSave={saveManualWell}
                />
              ) : (
                <div className="empty-state">Select an occupied target well to edit it.</div>
              )}

              <h3>Contents</h3>
              <VolumeEditor
                key={wellContentsForSelection.map((row) => `${row.well}-${row.liquid_name}-${row.volume_ul}`).join("|")}
                rows={wellContentsForSelection}
                saving={busyAction === "manual-volumes"}
                disabled={Boolean(busyAction)}
                onSave={saveManualVolumes}
              />
            </>
          )}

          <h3>Correction Chat</h3>
          <ChatLog messages={state?.correction_chat ?? []} />
        </aside>
      </section>

      <section className="data-dock">
        <div className="tabs" role="tablist" aria-label="Experiment tables">
          <TabButton active={activeDataTab === "validation"} onClick={() => setActiveDataTab("validation")}>
            Validation
          </TabButton>
          <TabButton active={activeDataTab === "contents"} onClick={() => setActiveDataTab("contents")}>
            Well Contents
          </TabButton>
          <TabButton active={activeDataTab === "sources"} onClick={() => setActiveDataTab("sources")}>
            Source Map
          </TabButton>
          <TabButton active={activeDataTab === "transfers"} onClick={() => setActiveDataTab("transfers")}>
            Transfers
          </TabButton>
          <TabButton active={activeDataTab === "diff"} onClick={() => setActiveDataTab("diff")}>
            Pending Diff
          </TabButton>
        </div>

        {activeDataTab === "validation" ? (
          <DataTable
            rows={state?.validation_report ?? []}
            columns={["check_name", "status", "severity", "detail"]}
            emptyText="No validation report yet."
          />
        ) : null}
        {activeDataTab === "contents" ? (
          <DataTable
            rows={state?.well_contents ?? []}
            columns={["well", "label", "liquid_name", "liquid_role", "volume_ul", "source_labware", "source_well"]}
            emptyText="No well contents yet."
          />
        ) : null}
        {activeDataTab === "sources" ? (
          <DataTable
            rows={state?.source_map ?? []}
            columns={["liquid_name", "liquid_role", "source_labware", "source_well", "available_volume_ul", "required_volume_ul", "status"]}
            emptyText="No source map yet."
          />
        ) : null}
        {activeDataTab === "transfers" ? (
          <DataTable
            rows={state?.transfer_table ?? []}
            columns={["step", "source_labware", "source_well", "destination_labware", "destination_well", "liquid_name", "volume_ul"]}
            emptyText="No transfer table yet."
          />
        ) : null}
        {activeDataTab === "diff" ? (
          <DataTable
            rows={state?.pending_diff ?? []}
            columns={["change_type", "well", "before_well", "after_well", "label"]}
            emptyText="No pending diff."
          />
        ) : null}

        <details className="debug-json">
          <summary>Raw response</summary>
          <pre>{experiment ? JSON.stringify(experiment, null, 2) : "No response yet."}</pre>
        </details>
      </section>
    </main>
  );
}

function Metric({ label, value }: { label: string; value: string | number }) {
  return (
    <div>
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function ManualWellEditor({
  row,
  saving,
  disabled,
  onSave
}: {
  row: PlateLayoutRow;
  saving: boolean;
  disabled: boolean;
  onSave: (edit: ManualWellEdit) => Promise<void>;
}) {
  const [well, setWell] = useState(row.well);
  const [label, setLabel] = useState(row.label);
  const [sample, setSample] = useState(String(row.sample ?? ""));
  const [condition, setCondition] = useState(String(row.condition ?? ""));
  const [replicate, setReplicate] = useState(Number(row.replicate ?? 1));
  const [assayType, setAssayType] = useState(String(row.assay_type ?? ""));

  return (
    <div className="manual-well-editor">
      <div className="manual-editor-grid">
        <div>
          <label htmlFor="manual-well">Destination well</label>
          <input id="manual-well" value={well} maxLength={3} onChange={(event) => setWell(event.target.value.toUpperCase())} />
        </div>
        <div>
          <label htmlFor="manual-replicate">Replicate</label>
          <input
            id="manual-replicate"
            type="number"
            min="1"
            value={replicate}
            onChange={(event) => setReplicate(Math.max(1, Number(event.target.value) || 1))}
          />
        </div>
      </div>
      <label htmlFor="manual-label">Label</label>
      <input id="manual-label" value={label} onChange={(event) => setLabel(event.target.value)} />
      <div className="manual-editor-grid">
        <div>
          <label htmlFor="manual-sample">Sample</label>
          <input id="manual-sample" value={sample} onChange={(event) => setSample(event.target.value)} />
        </div>
        <div>
          <label htmlFor="manual-condition">Condition</label>
          <input id="manual-condition" value={condition} onChange={(event) => setCondition(event.target.value)} />
        </div>
      </div>
      <label htmlFor="manual-assay-type">Assay type</label>
      <input id="manual-assay-type" value={assayType} onChange={(event) => setAssayType(event.target.value)} />
      <button
        className="full-width manual-save"
        disabled={disabled || !well.trim()}
        onClick={() =>
          onSave({
            original_well: row.well,
            well,
            label,
            sample,
            condition,
            replicate,
            assay_type: assayType
          })
        }
      >
        {saving ? "Saving..." : "Save Well"}
      </button>
    </div>
  );
}

function SourceVolumeSummary({ source }: { source: SourceMapRow }) {
  const remaining = Number(source.available_volume_ul) - Number(source.required_volume_ul);
  return (
    <dl className="source-volume-summary">
      <div>
        <dt>Original volume</dt>
        <dd>{formatVolume(source.available_volume_ul)} uL</dd>
      </div>
      <div>
        <dt>Planned aspiration</dt>
        <dd>{formatVolume(source.required_volume_ul)} uL</dd>
      </div>
      <div>
        <dt>Remaining</dt>
        <dd className={remaining < 0 ? "negative-volume" : ""}>{formatVolume(remaining)} uL</dd>
      </div>
    </dl>
  );
}

function ManualSourceEditor({
  source,
  saving,
  disabled,
  onSave
}: {
  source: SourceMapRow;
  saving: boolean;
  disabled: boolean;
  onSave: (edit: ManualSourceEdit) => Promise<void>;
}) {
  const [sourceLabware, setSourceLabware] = useState(source.source_labware);
  const [sourceWell, setSourceWell] = useState(source.source_well);
  const [availableVolume, setAvailableVolume] = useState(Number(source.available_volume_ul));

  return (
    <div className="manual-well-editor">
      <label>Liquid</label>
      <input value={source.liquid_name} disabled />
      <label htmlFor="manual-source-labware">Source labware</label>
      <input id="manual-source-labware" value={sourceLabware} onChange={(event) => setSourceLabware(event.target.value)} />
      <div className="manual-editor-grid">
        <div>
          <label htmlFor="manual-source-well">Source well</label>
          <input id="manual-source-well" value={sourceWell} maxLength={3} onChange={(event) => setSourceWell(event.target.value.toUpperCase())} />
        </div>
        <div>
          <label htmlFor="manual-source-volume">Original volume (uL)</label>
          <input
            id="manual-source-volume"
            type="number"
            min="0"
            step="0.1"
            value={availableVolume}
            onChange={(event) => setAvailableVolume(Math.max(0, Number(event.target.value) || 0))}
          />
        </div>
      </div>
      <button
        className="full-width manual-save"
        disabled={disabled || !sourceLabware.trim() || !sourceWell.trim()}
        onClick={() =>
          onSave({
            liquid_name: source.liquid_name,
            liquid_role: source.liquid_role,
            original_source_labware: source.source_labware,
            original_source_well: source.source_well,
            source_labware: sourceLabware,
            source_well: sourceWell,
            available_volume_ul: availableVolume
          })
        }
      >
        {saving ? "Saving..." : "Save Source"}
      </button>
    </div>
  );
}

function VolumeEditor({
  rows: volumeRows,
  showTarget = false,
  saving,
  disabled,
  onSave
}: {
  rows: WellContentRow[];
  showTarget?: boolean;
  saving: boolean;
  disabled: boolean;
  onSave: (updates: ManualVolumeUpdate[]) => Promise<void>;
}) {
  const [volumes, setVolumes] = useState(() => volumeRows.map((row) => Number(row.volume_ul)));

  if (!volumeRows.length) {
    return <div className="empty-state">No transfer volumes to edit.</div>;
  }

  return (
    <div className="volume-editor">
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              {showTarget ? <th>Target</th> : null}
              <th>Liquid</th>
              <th>Volume (uL)</th>
            </tr>
          </thead>
          <tbody>
            {volumeRows.map((row, index) => (
              <tr key={`${row.well}-${row.liquid_name}-${row.liquid_role}-${row.source_labware}-${row.source_well}`}>
                {showTarget ? <td>{row.well}</td> : null}
                <td>{row.liquid_name}</td>
                <td>
                  <input
                    aria-label={`${row.liquid_name} to ${row.well} volume`}
                    type="number"
                    min="0"
                    step="0.1"
                    value={volumes[index]}
                    onChange={(event) => {
                      const next = [...volumes];
                      next[index] = Math.max(0, Number(event.target.value) || 0);
                      setVolumes(next);
                    }}
                  />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <button
        className="full-width manual-save"
        disabled={disabled}
        onClick={() =>
          onSave(
            volumeRows.map((row, index) => ({
              well: row.well,
              liquid_name: row.liquid_name,
              liquid_role: row.liquid_role,
              source_labware: row.source_labware,
              source_well: row.source_well,
              volume_ul: volumes[index]
            }))
          )
        }
      >
        {saving ? "Saving..." : showTarget ? "Save Dispense Volumes" : "Save Well Volumes"}
      </button>
    </div>
  );
}

function TabButton({ active, children, onClick }: { active: boolean; children: React.ReactNode; onClick: () => void }) {
  return (
    <button className={active ? "tab active" : "tab"} onClick={onClick} type="button">
      {children}
    </button>
  );
}

function PlateLegend({ items, emptyText }: { items: LegendItem[]; emptyText: string }) {
  if (!items.length) {
    return <div className="plate-legend empty-legend">{emptyText}</div>;
  }
  return (
    <div className="plate-legend" aria-label="Plate color legend">
      {items.map((item) => (
        <span key={item.label}>
          <i className={`legend-swatch ${item.className}`} />
          {item.label}
        </span>
      ))}
    </div>
  );
}

function PlateGrid({
  kind,
  plateLayout = [],
  sourceMap = [],
  selectedWell,
  selectedSourceLabware,
  selectedSource,
  onSelectSource,
  onSelectWell
}: {
  kind: "target" | "source";
  plateLayout?: PlateLayoutRow[];
  sourceMap?: SourceMapRow[];
  selectedWell?: string;
  selectedSourceLabware?: string;
  selectedSource?: SourceMapRow;
  onSelectSource?: (source: SourceMapRow) => void;
  onSelectWell?: (well: string) => void;
}) {
  return (
    <div className="plate-grid" role="grid" aria-label={`${kind} plate`}>
      <div className="plate-corner" />
      {columns.map((column) => (
        <div className="plate-header" key={column}>
          {column}
        </div>
      ))}
      {rows.map((row) => (
        <Fragment key={row}>
          <div className="plate-header row-header" key={`${row}-header`}>
            {row}
          </div>
          {columns.map((column) => {
            const well = wellAddress(row, column);
            const target = plateLayout.find((item) => item.well === well);
            const source = sourceMap.find(
              (item) => item.source_well === well && item.source_labware === selectedSourceLabware
            );
            const occupied = Boolean(target || source);
            const label = target?.label ?? source?.liquid_name ?? "";
            const renderClass = kind === "source" ? sourceRenderClass(source) : targetRenderClass(target);
            const sublabel = source
              ? `Available ${formatVolume(source.available_volume_ul)} uL; required ${formatVolume(source.required_volume_ul)} uL`
              : target
                ? [target.sample, target.condition].filter(Boolean).join(" | ")
                : "";
            const isSelected = selectedWell === well || Boolean(
              source &&
              selectedSource?.liquid_name === source.liquid_name &&
              selectedSource?.source_labware === source.source_labware &&
              selectedSource?.source_well === source.source_well
            );
            const titleText = label ? `${well}: ${label}${sublabel ? ` (${sublabel})` : ""}` : well;
            return (
              <button
                type="button"
                className={`well ${occupied ? "occupied" : ""} ${kind === "source" ? "source-well" : "target-well"} ${renderClass} ${isSelected ? "selected" : ""}`}
                key={well}
                onClick={() => (source ? onSelectSource?.(source) : onSelectWell?.(well))}
                disabled={kind === "source" ? !source : !target}
                title={titleText}
              >
                <span>{label || well}</span>
                {source ? (
                  <small className="source-volume">
                    <b>{formatVolume(source.available_volume_ul)} uL</b>
                    <em>{formatVolume(source.required_volume_ul)} uL</em>
                  </small>
                ) : sublabel ? (
                  <small>{sublabel}</small>
                ) : null}
              </button>
            );
          })}
        </Fragment>
      ))}
    </div>
  );
}

function DataTable({
  rows: tableRows,
  columns: tableColumns,
  emptyText
}: {
  rows: JsonObject[];
  columns: string[];
  emptyText: string;
}) {
  if (!tableRows.length) {
    return <div className="empty-state">{emptyText}</div>;
  }
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            {tableColumns.map((column) => (
              <th key={column}>{column}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {tableRows.map((row, rowIndex) => (
            <tr className={tableRowClass(row)} key={rowIndex}>
              {tableColumns.map((column) => (
                <td key={column}>{String(row[column] ?? "")}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function tableRowClass(row: JsonObject): string {
  const severity = String(row.severity ?? "").toLowerCase();
  const status = String(row.status ?? "").toLowerCase();
  if (status === "pass") {
    return "row-pass";
  }
  if (severity === "critical" || severity === "error") {
    return "row-error";
  }
  if (severity === "warning" || severity === "warn") {
    return "row-warning";
  }
  return "";
}

function ChatLog({ messages }: { messages: { role: string; message: string }[] }) {
  if (!messages.length) {
    return <div className="empty-state">No correction messages yet.</div>;
  }
  return (
    <div className="chat-log">
      {messages.slice(-4).map((message, index) => (
        <div className="chat-message" key={index}>
          <strong>{message.role}</strong>
          <p>{message.message}</p>
        </div>
      ))}
    </div>
  );
}
