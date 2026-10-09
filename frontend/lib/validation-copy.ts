/** Translate known default validator messages; custom/domain messages stay intact. */
export function validationCopy(message: string | undefined, locale: "vi" | "en") {
  if (!message || locale === "en") return message;
  const exact: Record<string, string> = {
    Required: "Vui lòng nhập trường này.", "Invalid input": "Giá trị không hợp lệ.",
    "Invalid email": "Email không hợp lệ.", "Expected number, received nan": "Vui lòng nhập một số hợp lệ.",
    "Expected integer, received float": "Vui lòng nhập số nguyên.",
    "FPS must be 24, 25, or 30": "FPS phải là 24, 25 hoặc 30.",
  };
  if (exact[message]) return exact[message];
  let match = message.match(/^String must contain at least (\d+) character\(s\)$/);
  if (match) return `Vui lòng nhập ít nhất ${match[1]} ký tự.`;
  match = message.match(/^String must contain at most (\d+) character\(s\)$/);
  if (match) return `Vui lòng nhập không quá ${match[1]} ký tự.`;
  match = message.match(/^Number must be greater than or equal to (.+)$/);
  if (match) return `Giá trị phải lớn hơn hoặc bằng ${match[1]}.`;
  match = message.match(/^Number must be less than or equal to (.+)$/);
  if (match) return `Giá trị phải nhỏ hơn hoặc bằng ${match[1]}.`;
  match = message.match(/^Number must be a multiple of (.+)$/);
  if (match) return `Giá trị phải chia hết cho ${match[1]}.`;
  return message;
}
