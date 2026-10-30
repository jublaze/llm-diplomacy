import type { DiplomacyReportBundleV1, JsonValue } from './types.ts';

export const AUDIENCE_METADATA_SCHEMA = 'diplomacy_report_audience_v1' as const;
export const AUDIENCE_POLICY_VERSION = 'diplomacy_audience_policy_v1' as const;
export type BundleAudienceMode = 'omniscient_research' | 'postgame_public' | 'full_postgame_public';

export type BundleAudienceMetadata = {
  schema_version: typeof AUDIENCE_METADATA_SCHEMA;
  policy_version: typeof AUDIENCE_POLICY_VERSION | 'diplomacy_full_postgame_release_v1' | 'diplomacy_terra_postgame_release_v1' | 'legacy_unversioned';
  mode: BundleAudienceMode;
  reader_description: string;
  approved_for_static_public_distribution: boolean;
  projection?: Record<string, JsonValue>;
  legacy_inference: boolean;
};

const object = (value: unknown): value is Record<string, any> => Boolean(value) && typeof value === 'object' && !Array.isArray(value);

export function resolveBundleAudience(bundle: Pick<DiplomacyReportBundleV1, 'extensions'> | Record<string, any>): BundleAudienceMetadata {
  const raw = object(bundle.extensions) ? bundle.extensions.audience : undefined;
  if (raw === undefined) {
    return {
      schema_version: AUDIENCE_METADATA_SCHEMA,
      policy_version: 'legacy_unversioned',
      mode: 'omniscient_research',
      reader_description: 'Legacy research bundle without explicit audience metadata; not approved for static public distribution.',
      approved_for_static_public_distribution: false,
      legacy_inference: true,
    };
  }
  if (!object(raw)) throw new Error('extensions.audience must be an object');
  if (raw.schema_version !== AUDIENCE_METADATA_SCHEMA) throw new Error(`unsupported audience metadata schema ${JSON.stringify(raw.schema_version)}`);
  if (raw.policy_version === 'diplomacy_terra_postgame_release_v1') {
    const candidate = bundle as Record<string, any>;
    const sources: Record<string, string> = {
      'terra61-1028-g1': 'aa1e43c7d00eda2e8fbb587deaed4550a17a0dd947459725cb69ef0a3c5a80f4',
      'terra61-1028-g2': 'd34ccccdf664c1127069664e64688875f81ea0034d8e1dc4ecb2ac95f3edfd76',
    };
    if (raw.mode !== 'full_postgame_public' || raw.approved_for_static_public_distribution !== true
      || typeof raw.reader_description !== 'string' || !raw.reader_description.trim()
      || !object(raw.projection) || raw.projection.generator !== 'publication/terra_postgame.py'
      || !object(candidate.source) || !Object.hasOwn(sources, candidate.source.experiment_id)
      || raw.projection.source_bundle_sha256 !== sources[candidate.source.experiment_id]
      || raw.projection.source_bundle_id !== candidate.bundle_id) throw new Error('Invalid Terra full-postgame release provenance');
    return { ...raw, legacy_inference: false } as BundleAudienceMetadata;
  }
  if (raw.policy_version === 'diplomacy_full_postgame_release_v1') {
    if (raw.mode !== 'full_postgame_public' || raw.approved_for_static_public_distribution !== true
      || typeof raw.reader_description !== 'string' || !raw.reader_description.trim()
      || !object(raw.projection) || raw.projection.generator !== 'publication/full_postgame.py'
      || raw.projection.source_bundle_sha256 !== '9a29c5ea3380c15710b6d45d9ac289212c98391fac7adef246fe1760e07146f7'
      || typeof raw.projection.source_bundle_id !== 'string') throw new Error('Invalid full-postgame release provenance');
    return { ...raw, legacy_inference: false } as BundleAudienceMetadata;
  }
  if (raw.policy_version !== AUDIENCE_POLICY_VERSION) throw new Error(`unsupported audience policy ${JSON.stringify(raw.policy_version)}`);
  if (!['omniscient_research', 'postgame_public'].includes(raw.mode)) throw new Error(`unknown audience mode ${JSON.stringify(raw.mode)}`);
  if (typeof raw.reader_description !== 'string' || !raw.reader_description.trim()) throw new Error('audience reader_description must be non-empty');
  const approved = raw.approved_for_static_public_distribution;
  if (typeof approved !== 'boolean' || approved !== (raw.mode === 'postgame_public')) throw new Error('audience static-distribution approval contradicts its mode');
  if (!object(raw.projection) || typeof raw.projection.generator !== 'string' || typeof raw.projection.source_bundle_id !== 'string' || !/^[a-f\d]{64}$/.test(raw.projection.source_bundle_sha256)) {
    throw new Error('audience projection provenance is incomplete');
  }
  return { ...raw, legacy_inference: false } as BundleAudienceMetadata;
}

export const isStaticPublicBundle = (bundle: Pick<DiplomacyReportBundleV1, 'extensions'> | Record<string, any>): boolean => {
  try {
    const audience = resolveBundleAudience(bundle);
    return audience.mode === 'postgame_public' && audience.approved_for_static_public_distribution && !audience.legacy_inference;
  } catch {
    return false;
  }
};
