CREATE TABLE tenants (
	id VARCHAR NOT NULL,
	buyer VARCHAR NOT NULL,
	merchant VARCHAR NOT NULL,
	seller VARCHAR NOT NULL,
	integration VARCHAR NOT NULL,
	account VARCHAR NOT NULL,
	PRIMARY KEY (id)
);

CREATE TABLE webhook_receipts (
	id VARCHAR NOT NULL,
	body_hash VARCHAR NOT NULL,
	payload JSONB NOT NULL,
	conflict BOOLEAN NOT NULL,
	received_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (id)
);

CREATE TABLE identities (
	username VARCHAR NOT NULL,
	tenant_id VARCHAR NOT NULL,
	beneficiary VARCHAR NOT NULL,
	password_hash VARCHAR NOT NULL,
	role VARCHAR NOT NULL,
	PRIMARY KEY (username),
	FOREIGN KEY(tenant_id) REFERENCES tenants (id)
);

CREATE INDEX ix_identities_tenant_id ON identities (tenant_id);

CREATE TABLE purchases (
	id VARCHAR NOT NULL,
	tenant_id VARCHAR NOT NULL,
	buyer VARCHAR NOT NULL,
	owner VARCHAR NOT NULL,
	seller VARCHAR NOT NULL,
	provider VARCHAR NOT NULL,
	account VARCHAR NOT NULL,
	integration VARCHAR NOT NULL,
	quote JSONB NOT NULL,
	amount BIGINT NOT NULL,
	currency VARCHAR NOT NULL,
	request_digest VARCHAR NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (tenant_id, id),
	CHECK (amount > 0 AND amount <= 1000000),
	FOREIGN KEY(tenant_id) REFERENCES tenants (id),
	FOREIGN KEY(owner) REFERENCES identities (username)
);

CREATE INDEX ix_purchases_owner ON purchases (owner);

CREATE INDEX ix_purchases_tenant_id ON purchases (tenant_id);

CREATE TABLE observations (
	id VARCHAR NOT NULL,
	tenant_id VARCHAR NOT NULL,
	purchase_id VARCHAR NOT NULL,
	source VARCHAR NOT NULL,
	provider VARCHAR NOT NULL,
	account VARCHAR NOT NULL,
	mode VARCHAR NOT NULL,
	payload JSONB NOT NULL,
	observed_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(tenant_id, purchase_id) REFERENCES purchases (tenant_id, id)
);

CREATE INDEX ix_observations_purchase_id ON observations (purchase_id);

CREATE INDEX ix_observations_tenant_id ON observations (tenant_id);

CREATE TABLE operations (
	id VARCHAR NOT NULL,
	tenant_id VARCHAR NOT NULL,
	purchase_id VARCHAR NOT NULL,
	kind VARCHAR NOT NULL,
	operation_key VARCHAR NOT NULL,
	target VARCHAR NOT NULL,
	amount BIGINT NOT NULL,
	payload JSONB NOT NULL,
	request_digest VARCHAR NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(tenant_id, purchase_id) REFERENCES purchases (tenant_id, id),
	UNIQUE (purchase_id, kind, operation_key),
	CHECK (amount > 0),
	UNIQUE (operation_key)
);

CREATE INDEX ix_operations_purchase_id ON operations (purchase_id);

CREATE INDEX ix_operations_tenant_id ON operations (tenant_id);

CREATE TABLE purchase_states (
	purchase_id VARCHAR NOT NULL,
	seller_order VARCHAR,
	provider_order VARCHAR,
	payment_id VARCHAR,
	preparation VARCHAR NOT NULL,
	provider_status VARCHAR NOT NULL,
	canonical_status VARCHAR NOT NULL,
	entitlement VARCHAR NOT NULL,
	exposed_at TIMESTAMP WITH TIME ZONE,
	dismissed_at TIMESTAMP WITH TIME ZONE,
	conflict BOOLEAN NOT NULL,
	PRIMARY KEY (purchase_id),
	FOREIGN KEY(purchase_id) REFERENCES purchases (id),
	UNIQUE (provider_order),
	UNIQUE (payment_id)
);

CREATE TABLE captures (
	id VARCHAR NOT NULL,
	tenant_id VARCHAR NOT NULL,
	purchase_id VARCHAR NOT NULL,
	provider VARCHAR NOT NULL,
	account VARCHAR NOT NULL,
	payment_id VARCHAR NOT NULL,
	amount BIGINT NOT NULL,
	observation_id VARCHAR NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(tenant_id, purchase_id) REFERENCES purchases (tenant_id, id),
	UNIQUE (purchase_id),
	UNIQUE (provider, account, payment_id),
	FOREIGN KEY(observation_id) REFERENCES observations (id)
);

CREATE INDEX ix_captures_observation_id ON captures (observation_id);

CREATE INDEX ix_captures_tenant_id ON captures (tenant_id);

CREATE TABLE operation_states (
	operation_id VARCHAR NOT NULL,
	committed_at TIMESTAMP WITH TIME ZONE,
	knowledge VARCHAR NOT NULL,
	resource_id VARCHAR,
	PRIMARY KEY (operation_id),
	FOREIGN KEY(operation_id) REFERENCES operations (id),
	UNIQUE (resource_id)
);
