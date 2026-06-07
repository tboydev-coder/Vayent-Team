import React from "react";

type BrandLogoProps = {
  alt?: string;
  className?: string;
};

const BrandLogo: React.FC<BrandLogoProps> = ({
  alt = "Vayent logo",
  className = "",
}) => {
  return <img src="/img/logo.jpeg" alt={alt} className={className} />;
};

export default BrandLogo;
