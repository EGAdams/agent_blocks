/** Architectural fitness rule for the Voice Communication dependency graph. */
export interface IVoiceDesignRule {
  acceptsDependency(sourceModule: string, targetModule: string): boolean;
}
