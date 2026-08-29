/** Strategy for transcript normalization before application policy runs. */
export interface ILanguageProcessor {
  process(transcript: string): Promise<string>;
}
