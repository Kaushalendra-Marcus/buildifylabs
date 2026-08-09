/**
 * UserMessage (specs/14 §4.1) — right-aligned, single line-height bubble, no
 * card chrome. An uploaded file (if any) shows as a small chip ABOVE the
 * bubble, not inside the answer echo (F5 supplies the fileName).
 */
import { FileText } from 'lucide-react';
import type { UserChatMessage } from '../chat-store';

export function UserMessage({ message }: { message: UserChatMessage }) {
  return (
    <div className="message message--user">
      {message.fileName ? (
        <span
          className="message__file-chip"
          title={`Uploaded: ${message.fileName}`}
        >
          <FileText size={13} aria-hidden="true" />
          {message.fileName}
        </span>
      ) : null}
      <div className="message__user-bubble">{message.content}</div>
    </div>
  );
}