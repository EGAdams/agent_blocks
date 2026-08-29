/** Narrow application port for Toyota's note-editing command channel. */
export interface INoteCommandChannel {
  submit(command: string): Promise<void>;
}
