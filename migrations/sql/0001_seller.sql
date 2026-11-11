CREATE TABLE consumptions (
	id VARCHAR NOT NULL,
	tenant_id VARCHAR NOT NULL,
	body_digest VARCHAR NOT NULL,
	result JSONB NOT NULL,
	PRIMARY KEY (id)
);

CREATE INDEX ix_consumptions_tenant_id ON consumptions (tenant_id);

CREATE TABLE events (
	id VARCHAR NOT NULL,
	tenant_id VARCHAR NOT NULL,
	kind VARCHAR NOT NULL,
	resource_id VARCHAR NOT NULL,
	detail JSONB NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (id)
);

CREATE INDEX ix_events_tenant_id ON events (tenant_id);

CREATE TABLE quotes (
	id VARCHAR NOT NULL,
	tenant_id VARCHAR NOT NULL,
	body JSONB NOT NULL,
	expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
	PRIMARY KEY (id)
);

CREATE INDEX ix_quotes_tenant_id ON quotes (tenant_id);

CREATE TABLE users (
	username VARCHAR NOT NULL,
	tenant_id VARCHAR NOT NULL,
	beneficiary VARCHAR NOT NULL,
	token_hash VARCHAR NOT NULL,
	PRIMARY KEY (username)
);

CREATE INDEX ix_users_tenant_id ON users (tenant_id);

CREATE TABLE orders (
	id VARCHAR NOT NULL,
	tenant_id VARCHAR NOT NULL,
	quote_id VARCHAR NOT NULL,
	purchase_id VARCHAR NOT NULL,
	terms JSONB NOT NULL,
	revision INTEGER NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (tenant_id, id),
	UNIQUE (quote_id),
	FOREIGN KEY(quote_id) REFERENCES quotes (id),
	UNIQUE (purchase_id)
);

CREATE INDEX ix_orders_tenant_id ON orders (tenant_id);

CREATE TABLE seats (
	id VARCHAR NOT NULL,
	tenant_id VARCHAR NOT NULL,
	order_id VARCHAR NOT NULL,
	beneficiary VARCHAR NOT NULL,
	active BOOLEAN NOT NULL,
	revision INTEGER NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(tenant_id, order_id) REFERENCES orders (tenant_id, id),
	UNIQUE (order_id, beneficiary)
);

CREATE INDEX ix_seats_beneficiary ON seats (beneficiary);

CREATE INDEX ix_seats_order_id ON seats (order_id);

CREATE INDEX ix_seats_tenant_id ON seats (tenant_id);

CREATE TABLE workspace_entries (
	id VARCHAR NOT NULL,
	seat_id VARCHAR NOT NULL,
	text TEXT NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(seat_id) REFERENCES seats (id)
);

CREATE INDEX ix_workspace_entries_seat_id ON workspace_entries (seat_id);
