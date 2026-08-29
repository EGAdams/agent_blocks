/** Facade composed at the Toyota/dashboard boundary. */
export interface IToyotaVoiceApplication {
  start(): Promise<void>;
  stop(): Promise<void>;
}
