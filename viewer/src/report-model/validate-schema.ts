import Ajv2020 from 'ajv/dist/2020.js';
import schema from '../../contracts/diplomacy_report_bundle_v1.schema.json' with { type: 'json' };

const validate = new Ajv2020({ allErrors: true, strict: false }).compile(schema);

export function validateSchema(bundle: unknown): string[] {
  return validate(bundle) ? [] : (validate.errors ?? []).map(error => `${error.instancePath || '/'}: ${error.message}`);
}
