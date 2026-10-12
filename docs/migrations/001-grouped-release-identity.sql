-- Apply once to an existing PostgreSQL deployment, after reviewing a backup.
-- Existing release IDs and all source/document rows are preserved.
BEGIN;
ALTER TABLE product_releases ADD COLUMN release_source_id VARCHAR(71);
UPDATE product_releases SET release_source_id = document_id;
ALTER TABLE product_releases ALTER COLUMN release_source_id SET NOT NULL;
ALTER TABLE product_releases DROP CONSTRAINT uq_release_product_document;
ALTER TABLE product_releases ADD CONSTRAINT uq_release_product_source
    UNIQUE (product_id, release_source_id);
COMMIT;
