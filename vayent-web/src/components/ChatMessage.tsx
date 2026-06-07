import React from "react";

interface Props {
  sql: string;
  result: unknown;
  explanation: string;
}

const ChatMessage: React.FC<Props> = ({ sql, result, explanation }) => {
  return (
    <div className="bg-surface p-4 rounded">
      <pre className="text-sm">{sql}</pre>
      <div>{JSON.stringify(result)}</div>
      <p className="text-xs italic">{explanation}</p>
    </div>
  );
};

export default ChatMessage;
