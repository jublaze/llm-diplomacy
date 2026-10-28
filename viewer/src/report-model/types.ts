/** Portable, postgame-only contract for a completed Diplomacy game. */
export type JsonValue = null | boolean | number | string | JsonValue[] | { [key: string]: JsonValue };

export interface EvidenceSource {
  source_id: string;
  artifact_path: string;
  json_pointer?: string;
  sha256: string;
  visibility: 'public' | 'private' | 'omniscient_postgame';
  kind: 'board_snapshot' | 'orders' | 'adjudication' | 'message' | 'private_record' | 'report' | 'manifest';
  /** Present only when private-at-the-time messages are deliberately released after play. */
  release_context?: 'postgame_released_negotiation_transcript';
}

export interface BoardState {
  supply_ownership: Record<string, string>;
  units: Array<Record<string, JsonValue>>;
  standoff_provinces: string[];
}

export interface ReportMessage {
  message_id: string;
  phase_id: string;
  chronological_position: number;
  sender_agent_id: string;
  recipient_agent_ids: string[];
  channel: 'private' | 'global' | 'group';
  thread_id: string;
  text: string;
  visibility: 'public' | 'private';
  source_ref: string;
}

export interface EditorialCitation {
  source_ref: string;
  label: string;
  evidence_type: 'public' | 'message' | 'private_record' | 'postgame';
  target: { type: 'source' | 'message' | 'record' | 'order'; id: string };
}

export interface EditorialClaim { text: string; citations: EditorialCitation[]; }
export interface EditorialSection { heading: string; claims: EditorialClaim[]; bridge: EditorialClaim; }
export interface EditorialArc { title: string; claims: EditorialClaim[]; }
export interface ReportEditorial {
  title?: string;
  executive_history?: EditorialClaim[];
  turning_points?: EditorialArc[];
  power_dossiers?: Array<{ power: string; agent_id: string; note: EditorialClaim }>;
}

export interface ReportPhase {
  phase_id: string;
  kind: string;
  year: number;
  season: 'spring' | 'fall' | 'winter';
  board_before: BoardState;
  board_after: BoardState;
  messages: string[];
  orders: string[];
  adjudication: Record<string, JsonValue>;
  private_records: string[];
  narrative: { overview: string; theater_sections: JsonValue[]; aftermath: string; source_refs: string[]; editorial?: { sections: EditorialSection[] } };
  source_refs: string[];
  /** Reserved for Batch B and future contract additions without a breaking change. */
  extensions: Record<string, JsonValue>;
}

export interface DiplomacyReportBundleV1 {
  schema_version: 'diplomacy_report_bundle_v1';
  bundle_id: string;
  source: { experiment_id: string; game_id: string; artifact_manifest_sha256: string; visibility_mode: 'public' | `player-${string}` | 'omniscient_postgame' };
  map: { map_id: string; geometry_version: string };
  game: Record<string, JsonValue>;
  roster: Array<Record<string, JsonValue>>;
  summary: Record<string, JsonValue> & { editorial?: ReportEditorial };
  strategic_arcs: EditorialArc[];
  years: Array<{ year: number; phases: ReportPhase[] }>;
  messages: Record<string, ReportMessage>;
  orders: Record<string, Record<string, JsonValue>>;
  private_records: Record<string, Record<string, JsonValue>>;
  evidence_index: Record<string, EvidenceSource>;
  extensions: Record<string, JsonValue>;
}
