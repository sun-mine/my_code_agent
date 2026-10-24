export const STATUS_LABEL: Record<string, string> = {
  idle:           '空闲',
  thinking:       '思考中',
  executing_tool: '执行工具',
}

/** Strip ANSI escape codes and other terminal control sequences from a string */
export function stripAnsi(str: string): string {
  return str
    // eslint-disable-next-line no-control-regex
    .replace(/\x1b\[[0-9;]*[a-zA-Z]/g, '')     // CSI sequences (colors, cursor moves)
    .replace(/\x1b\][0-9;]*[^\x07]*\x07/g, '')  // OSC sequences (title, hyperlinks)
    .replace(/\x1b[PX^_].*?\x1b\\/g, '')         // DCS, SOS, PM, APC sequences
    .replace(/[\x00-\x08\x0b\x0c\x0e-\x1f]/g, '') // other control chars (keep \n, \t)
    .replace(/\r\n/g, '\n')                       // CRLF → LF
    .replace(/\r/g, '\n')                         // CR → LF (progress bars)
    .replace(/\x08+\n/g, '\n')                     // backspace-then-newline (overwrite lines)
    .replace(/[^\S\n]+$/gm, '')                   // trailing whitespace per line
}
