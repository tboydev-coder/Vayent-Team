import React from "react";
import { Link } from "react-router-dom";

interface Props {
  id: string;
  name: string;
  db_type: string;
}

const ConnectionCard: React.FC<Props> = ({ id, name, db_type }) => {
  return (
    <Link to={`/connections/${id}/schema`}>
      <div className="p-4 bg-surface rounded hover:bg-accent/20">
        <h3 className="font-semibold">{name}</h3>
        <p className="text-sm">{db_type}</p>
      </div>
    </Link>
  );
};

export default ConnectionCard;
