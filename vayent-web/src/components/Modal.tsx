import React from "react";

interface Props {
  title?: string;
  children: React.ReactNode;
  onClose: () => void;
}

const Modal: React.FC<Props> = ({ title, children, onClose }) => {
  return (
    <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center">
      <div className="bg-surface p-6 rounded max-w-lg w-full">
        {title && <h2 className="text-xl font-bold mb-4">{title}</h2>}
        <div>{children}</div>
        <button className="mt-4 px-4 py-2 border rounded" onClick={onClose}>
          Close
        </button>
      </div>
    </div>
  );
};

export default Modal;
