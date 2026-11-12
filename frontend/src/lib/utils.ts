export const STATUS_LABEL: Record<string, string> = {
  idle:           '空闲',
  thinking:       '思考中',
  executing_tool: '执行工具',
  reviewing:      'A2A 审查中',
  cancelling:     '停止中',
}

/** Strip ANSI escape codes and other terminal control sequences from a string */
export function stripAnsi(str: string): string {
  let clean = ''
  for (let index = 0; index < str.length; index += 1) {
    const code = str.charCodeAt(index)
    if (code === 0x1b) {
      const kind = str[index + 1]
      if (kind === '[') {
        index += 2
        while (index < str.length) {
          const byte = str.charCodeAt(index)
          if (byte >= 0x40 && byte <= 0x7e) break
          index += 1
        }
      } else if (kind === ']' || kind === 'P' || kind === 'X' || kind === '^' || kind === '_') {
        index += 2
        while (index < str.length) {
          if (str.charCodeAt(index) === 0x07) break
          if (str.charCodeAt(index) === 0x1b && str[index + 1] === '\\') {
            index += 1
            break
          }
          index += 1
        }
      }
      continue
    }
    if (code === 0x0d) {
      if (str.charCodeAt(index + 1) === 0x0a) index += 1
      clean += '\n'
      continue
    }
    if (code <= 0x08 || code === 0x0b || code === 0x0c || (code >= 0x0e && code <= 0x1f)) continue
    clean += str[index]
  }
  return clean.replace(/[^\S\n]+$/gm, '')
}
