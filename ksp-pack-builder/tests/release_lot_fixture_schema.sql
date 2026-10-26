\set ON_ERROR_STOP on

BEGIN;

CREATE TABLE concept (
  concept_id TEXT PRIMARY KEY,
  domain TEXT NOT NULL CHECK (domain IN ('PHARM','MEDSURG','BRIDGE')),
  canonical_label TEXT NOT NULL,
  status TEXT NOT NULL
);

CREATE TABLE claim (
  claim_id TEXT PRIMARY KEY CHECK (claim_id ~ '^(pharm|medsurg|bridge):claim:[A-Za-z0-9._-]+$'),
  domain TEXT NOT NULL CHECK (domain IN ('PHARM','MEDSURG','BRIDGE')),
  concept_id TEXT REFERENCES concept(concept_id)
);

CREATE TABLE claim_revision (
  claim_revision_id TEXT PRIMARY KEY CHECK (claim_revision_id ~ '^(pharm|medsurg|bridge):claimrev:[A-Za-z0-9._-]+$'),
  claim_id TEXT NOT NULL REFERENCES claim(claim_id),
  revision_number INT NOT NULL CHECK (revision_number >= 1),
  claim_type TEXT NOT NULL,
  claim_text TEXT NOT NULL,
  context_frame JSONB NOT NULL,
  verification_mode TEXT NOT NULL CHECK (verification_mode IN ('MACHINE_ENTAILED','HUMAN_DUAL','HUMAN_RN_AUTHORED_RULE')),
  safety_class TEXT NOT NULL CHECK (safety_class IN ('ROUTINE','HIGH_RISK','SAFETY_CRITICAL')),
  authority_type TEXT NOT NULL,
  content_hash CHAR(64) NOT NULL CHECK (content_hash ~ '^[0-9A-Fa-f]{64}$'),
  status TEXT NOT NULL CHECK (status IN ('CANDIDATE','SUPPORTED','CONFLICT_REVIEW','BLOCKED_EVIDENCE','BLOCKED_RIGHTS','BLOCKED_REVIEW','APPROVED','STALE','RECALLED')),
  recalled BOOLEAN NOT NULL DEFAULT false,
  was_approved BOOLEAN NOT NULL DEFAULT false,
  UNIQUE (claim_id, revision_number),
  UNIQUE (claim_revision_id, content_hash),
  CHECK (NOT recalled OR status='RECALLED')
);

CREATE FUNCTION mark_approved_revision() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.status='APPROVED' THEN NEW.was_approved := true; END IF;
  IF TG_OP='UPDATE' AND OLD.was_approved AND NOT NEW.was_approved THEN
    RAISE EXCEPTION 'was_approved cannot be cleared';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER trg_mark_approved_revision
BEFORE INSERT OR UPDATE ON claim_revision
FOR EACH ROW EXECUTE FUNCTION mark_approved_revision();

CREATE TABLE release_lot (
  lot_id TEXT PRIMARY KEY,
  bundle_sha256 CHAR(64) NOT NULL CHECK (bundle_sha256 ~ '^[0-9A-Fa-f]{64}$'),
  frozen_at TIMESTAMPTZ NOT NULL,
  registry_snapshot_at TIMESTAMPTZ NOT NULL,
  snapshot_isolation TEXT NOT NULL DEFAULT 'REPEATABLE READ' CHECK (snapshot_isolation='REPEATABLE READ'),
  status TEXT NOT NULL CHECK (status IN ('CANDIDATE','FROZEN','RELEASED','QUARANTINED','RECALLED'))
);

CREATE TABLE release_lot_claim (
  lot_id TEXT NOT NULL REFERENCES release_lot(lot_id),
  claim_revision_id TEXT NOT NULL,
  content_hash CHAR(64) NOT NULL CHECK (content_hash ~ '^[0-9A-Fa-f]{64}$'),
  PRIMARY KEY (lot_id, claim_revision_id),
  FOREIGN KEY (claim_revision_id, content_hash)
    REFERENCES claim_revision(claim_revision_id, content_hash)
);

COMMIT;
