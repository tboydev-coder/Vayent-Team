import React, { useEffect, useState } from "react";

import {
  TRUST_CENTER_SECTIONS,
  TRUST_CENTER_UPDATED_AT,
  getTrustCenterSection,
  type TrustCenterSectionId,
} from "../content/trustCenter";
import "../styles/trustCenter.css";

type TrustCenterModalProps = {
  initialSection?: TrustCenterSectionId;
  onClose: () => void;
};

const TrustCenterModal: React.FC<TrustCenterModalProps> = ({
  initialSection = "privacy",
  onClose,
}) => {
  const [activeSectionId, setActiveSectionId] =
    useState<TrustCenterSectionId>(initialSection);
  const activeSection = getTrustCenterSection(activeSectionId);

  useEffect(() => {
    setActiveSectionId(initialSection);
  }, [initialSection]);

  useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        onClose();
      }
    };

    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [onClose]);

  return (
    <div
      className="trust-modal-overlay"
      role="presentation"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) {
          onClose();
        }
      }}
    >
      <section
        className="trust-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="trust-modal-title"
      >
        <div className="trust-modal-sidebar">
          <div>
            <p className="trust-kicker">Trust Center</p>
            <h2>Vayent policies</h2>
            <p>
              Privacy, security, and responsible-use terms for teams connecting
              business databases.
            </p>
          </div>

          <div className="trust-modal-tabs" role="tablist" aria-label="Trust policies">
            {TRUST_CENTER_SECTIONS.map((section) => (
              <button
                key={section.id}
                type="button"
                role="tab"
                aria-selected={activeSection.id === section.id}
                className={activeSection.id === section.id ? "is-active" : ""}
                onClick={() => setActiveSectionId(section.id)}
              >
                {section.label}
              </button>
            ))}
          </div>
        </div>

        <div className="trust-modal-content">
          <div className="trust-modal-head">
            <div>
              <p className="trust-kicker">Last updated {TRUST_CENTER_UPDATED_AT}</p>
              <h3 id="trust-modal-title">{activeSection.title}</h3>
            </div>
            <button
              type="button"
              className="trust-modal-close"
              aria-label="Close trust center"
              onClick={onClose}
            >
              x
            </button>
          </div>

          <p className="trust-modal-summary">{activeSection.summary}</p>

          <div className="trust-policy-stack">
            {activeSection.groups.map((group) => (
              <article className="trust-policy-block" key={group.title}>
                <h4>{group.title}</h4>
                {group.body ? <p>{group.body}</p> : null}
                {group.items ? (
                  <ul>
                    {group.items.map((item) => (
                      <li key={item}>{item}</li>
                    ))}
                  </ul>
                ) : null}
              </article>
            ))}
          </div>

          <div className="trust-modal-foot">
            <strong>Important</strong>
            <span>
              This product policy explains how Vayent is intended to operate.
              Signed contracts, data processing agreements, and deployment
              settings control where they apply.
            </span>
          </div>
        </div>
      </section>
    </div>
  );
};

type TrustCenterLinksProps = {
  className?: string;
  sections?: TrustCenterSectionId[];
};

export const TrustCenterLinks: React.FC<TrustCenterLinksProps> = ({
  className = "",
  sections = ["privacy", "security", "terms"],
}) => {
  const [activeSection, setActiveSection] =
    useState<TrustCenterSectionId | null>(null);

  return (
    <>
      <span className={`trust-link-set ${className}`}>
        {sections.map((sectionId) => {
          const section = getTrustCenterSection(sectionId);
          return (
            <button
              key={section.id}
              type="button"
              className="trust-link"
              onClick={() => setActiveSection(section.id)}
            >
              {section.label}
            </button>
          );
        })}
      </span>

      {activeSection ? (
        <TrustCenterModal
          initialSection={activeSection}
          onClose={() => setActiveSection(null)}
        />
      ) : null}
    </>
  );
};

export default TrustCenterModal;
