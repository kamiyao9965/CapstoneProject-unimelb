
CREATE TABLE travel_product_details (
	release_id VARCHAR(71) NOT NULL, 
	trip_frequency TEXT, 
	rental_vehicle_excess_limit_aud NUMERIC, 
	personal_liability_limit_aud NUMERIC, 
	attributes JSONB NOT NULL, 
	PRIMARY KEY (release_id), 
	FOREIGN KEY(release_id) REFERENCES product_releases (release_id), 
	CONSTRAINT ck_travel_product_details_75769f66bf CHECK (trip_frequency IN ('single_trip', 'annual_multi_trip'))
);
