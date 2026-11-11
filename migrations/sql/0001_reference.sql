CREATE TABLE clock (
	id SERIAL NOT NULL,
	tick BIGINT NOT NULL,
	PRIMARY KEY (id)
);

CREATE TABLE resources (
	id VARCHAR NOT NULL,
	kind VARCHAR NOT NULL,
	request_digest VARCHAR NOT NULL,
	body JSONB NOT NULL,
	PRIMARY KEY (id)
);

CREATE INDEX ix_resources_kind ON resources (kind);
