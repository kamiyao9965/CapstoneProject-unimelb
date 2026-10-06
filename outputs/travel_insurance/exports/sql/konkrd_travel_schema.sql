--
-- PostgreSQL database dump
--

\restrict oXJRoH1jMlA4Kd5C2d7PgkoMoMyeSvmgqQ9f2Tqk6L28bST7lo3vZQ3Q3OQDlf8

-- Dumped from database version 17.11 (Homebrew)
-- Dumped by pg_dump version 17.11 (Homebrew)

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET transaction_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: documents; Type: TABLE; Schema: public; Owner: yangyajing
--

CREATE TABLE public.documents (
    document_id character varying(71) NOT NULL,
    insurer_code character varying(64) NOT NULL,
    sha256 character varying(64) NOT NULL,
    document_type character varying(32),
    title text,
    source_path text NOT NULL,
    source_url text,
    effective_from date,
    metadata_json jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.documents OWNER TO yangyajing;

--
-- Name: extraction_runs; Type: TABLE; Schema: public; Owner: yangyajing
--

CREATE TABLE public.extraction_runs (
    run_id character varying(128) NOT NULL,
    document_id character varying(71) NOT NULL,
    schema_version_id character varying(71) NOT NULL,
    provider character varying(64) NOT NULL,
    model character varying(128) NOT NULL,
    status character varying(32) NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.extraction_runs OWNER TO yangyajing;

--
-- Name: insurers; Type: TABLE; Schema: public; Owner: yangyajing
--

CREATE TABLE public.insurers (
    code character varying(64) NOT NULL,
    display_name text,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.insurers OWNER TO yangyajing;

--
-- Name: product_release_documents; Type: TABLE; Schema: public; Owner: yangyajing
--

CREATE TABLE public.product_release_documents (
    release_id character varying(71) NOT NULL,
    document_id character varying(71) NOT NULL,
    document_role character varying(32) NOT NULL
);


ALTER TABLE public.product_release_documents OWNER TO yangyajing;

--
-- Name: product_releases; Type: TABLE; Schema: public; Owner: yangyajing
--

CREATE TABLE public.product_releases (
    release_id character varying(71) NOT NULL,
    product_id character varying(71) NOT NULL,
    document_id character varying(71) NOT NULL,
    schema_version_id character varying(71) NOT NULL,
    source_product_type character varying(64) NOT NULL,
    effective_from date,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.product_releases OWNER TO yangyajing;

--
-- Name: products; Type: TABLE; Schema: public; Owner: yangyajing
--

CREATE TABLE public.products (
    product_id character varying(71) NOT NULL,
    vertical_code character varying(64) NOT NULL,
    insurer_code character varying(64) NOT NULL,
    canonical_name text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.products OWNER TO yangyajing;

--
-- Name: raw_extractions; Type: TABLE; Schema: public; Owner: yangyajing
--

CREATE TABLE public.raw_extractions (
    run_id character varying(128) NOT NULL,
    artifact jsonb NOT NULL,
    payload_sha256 character varying(64) NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.raw_extractions OWNER TO yangyajing;

--
-- Name: schema_versions; Type: TABLE; Schema: public; Owner: yangyajing
--

CREATE TABLE public.schema_versions (
    schema_version_id character varying(71) NOT NULL,
    vertical_code character varying(64) NOT NULL,
    version character varying(128) NOT NULL,
    schema_payload jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.schema_versions OWNER TO yangyajing;

--
-- Name: travel_product_details; Type: TABLE; Schema: public; Owner: yangyajing
--

CREATE TABLE public.travel_product_details (
    release_id character varying(71) NOT NULL,
    brand_name text,
    insurer_name text,
    issuer_or_agent_name text,
    plan_name text,
    cover_level text,
    pds_title text,
    pds_effective_date text,
    pds_prepared_or_version_date text,
    geographic_scope text,
    trip_frequency text,
    annual_unlimited_trips boolean,
    maximum_trip_duration_options text,
    maximum_trip_duration_days numeric,
    policy_period_months numeric,
    minimum_distance_from_home_km numeric,
    paid_accommodation_exception boolean,
    eligibility_rules text,
    minimum_age_years numeric,
    maximum_age_years numeric,
    dependent_child_age_limit_years numeric,
    age_eligibility_notes text,
    already_travelling_or_overseas_available boolean,
    waiting_period_hours numeric,
    cooling_off_period_days numeric,
    policy_cancellation_refund_terms text,
    excess_options text,
    standard_excess_aud numeric,
    emergency_medical_limit text,
    emergency_dental_limit text,
    cancellation_cover_limit text,
    cancellation_cover_selectable boolean,
    additional_expenses_or_trip_disruption_limit text,
    travel_delay_limit text,
    luggage_limit text,
    luggage_single_item_limit text,
    electronics_or_valuables_limit text,
    luggage_delay_limit text,
    travel_documents_limit text,
    money_limit text,
    rental_vehicle_excess_limit text,
    personal_liability_limit text,
    personal_accident_limit text,
    loss_of_income_limit text,
    covid_cover_available boolean,
    covid_medical_limit text,
    covid_cancellation_or_disruption_limit text,
    covid_cover_summary text,
    cruise_cover_status text,
    cruise_cover_required_for_cruises boolean,
    limit_basis_notes text,
    annual_limit_reinstatement boolean,
    pre_existing_medical_cover_status text,
    medical_assessment_required boolean,
    pre_existing_medical_conditions_summary text,
    pregnancy_cover_summary text,
    pregnancy_single_week_limit numeric,
    pregnancy_multiple_week_limit numeric,
    non_traveller_health_rules text,
    rental_vehicle_excess_conditions text,
    luggage_conditions text,
    business_cover_details text,
    inbound_cover_details text,
    travel_warning_and_sanctions_rules text,
    claims_process_summary text,
    emergency_assistance_details text,
    premium_factors text,
    source_pdf_path text,
    source_pdf_hash text,
    claim_notification_deadline_days numeric,
    legal_expenses_limit text,
    natural_disaster_cover_status text,
    medical_expenses_max_months_from_onset numeric,
    repatriation_of_remains_limit text,
    travel_delay_minimum_delay_hours numeric,
    travel_services_provider_insolvency_limit text,
    winter_sports_cover_status text,
    attributes jsonb NOT NULL,
    CONSTRAINT ck_travel_product_details_13b21bd62e CHECK ((natural_disaster_cover_status = ANY (ARRAY['included'::text, 'optional_add_on'::text, 'conditionally_included'::text, 'excluded'::text, 'not_applicable'::text, 'unknown'::text]))),
    CONSTRAINT ck_travel_product_details_15eae122a0 CHECK ((pre_existing_medical_cover_status = ANY (ARRAY['not_covered_unless_approved'::text, 'some_conditions_automatically_included'::text, 'covered_if_screened_and_approved'::text, 'not_available'::text, 'unknown'::text]))),
    CONSTRAINT ck_travel_product_details_75769f66bf CHECK ((trip_frequency = ANY (ARRAY['single_trip'::text, 'annual_multi_trip'::text, 'frequent_traveller'::text, 'already_overseas'::text, 'unknown'::text]))),
    CONSTRAINT ck_travel_product_details_a305b6d777 CHECK ((cruise_cover_status = ANY (ARRAY['included'::text, 'optional_add_on'::text, 'mandatory_for_cruise'::text, 'excluded'::text, 'not_applicable'::text, 'unknown'::text]))),
    CONSTRAINT ck_travel_product_details_aeedde9804 CHECK ((winter_sports_cover_status = ANY (ARRAY['included'::text, 'optional_add_on'::text, 'separate_plan_or_tier'::text, 'excluded'::text, 'not_applicable'::text, 'unknown'::text])))
);


ALTER TABLE public.travel_product_details OWNER TO yangyajing;

--
-- Name: verticals; Type: TABLE; Schema: public; Owner: yangyajing
--

CREATE TABLE public.verticals (
    code character varying(64) NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.verticals OWNER TO yangyajing;

--
-- Name: documents documents_pkey; Type: CONSTRAINT; Schema: public; Owner: yangyajing
--

ALTER TABLE ONLY public.documents
    ADD CONSTRAINT documents_pkey PRIMARY KEY (document_id);


--
-- Name: documents documents_sha256_key; Type: CONSTRAINT; Schema: public; Owner: yangyajing
--

ALTER TABLE ONLY public.documents
    ADD CONSTRAINT documents_sha256_key UNIQUE (sha256);


--
-- Name: extraction_runs extraction_runs_pkey; Type: CONSTRAINT; Schema: public; Owner: yangyajing
--

ALTER TABLE ONLY public.extraction_runs
    ADD CONSTRAINT extraction_runs_pkey PRIMARY KEY (run_id);


--
-- Name: insurers insurers_pkey; Type: CONSTRAINT; Schema: public; Owner: yangyajing
--

ALTER TABLE ONLY public.insurers
    ADD CONSTRAINT insurers_pkey PRIMARY KEY (code);


--
-- Name: product_release_documents product_release_documents_pkey; Type: CONSTRAINT; Schema: public; Owner: yangyajing
--

ALTER TABLE ONLY public.product_release_documents
    ADD CONSTRAINT product_release_documents_pkey PRIMARY KEY (release_id, document_id);


--
-- Name: product_releases product_releases_pkey; Type: CONSTRAINT; Schema: public; Owner: yangyajing
--

ALTER TABLE ONLY public.product_releases
    ADD CONSTRAINT product_releases_pkey PRIMARY KEY (release_id);


--
-- Name: products products_pkey; Type: CONSTRAINT; Schema: public; Owner: yangyajing
--

ALTER TABLE ONLY public.products
    ADD CONSTRAINT products_pkey PRIMARY KEY (product_id);


--
-- Name: raw_extractions raw_extractions_pkey; Type: CONSTRAINT; Schema: public; Owner: yangyajing
--

ALTER TABLE ONLY public.raw_extractions
    ADD CONSTRAINT raw_extractions_pkey PRIMARY KEY (run_id);


--
-- Name: schema_versions schema_versions_pkey; Type: CONSTRAINT; Schema: public; Owner: yangyajing
--

ALTER TABLE ONLY public.schema_versions
    ADD CONSTRAINT schema_versions_pkey PRIMARY KEY (schema_version_id);


--
-- Name: travel_product_details travel_product_details_pkey; Type: CONSTRAINT; Schema: public; Owner: yangyajing
--

ALTER TABLE ONLY public.travel_product_details
    ADD CONSTRAINT travel_product_details_pkey PRIMARY KEY (release_id);


--
-- Name: products uq_product_vertical_insurer_name; Type: CONSTRAINT; Schema: public; Owner: yangyajing
--

ALTER TABLE ONLY public.products
    ADD CONSTRAINT uq_product_vertical_insurer_name UNIQUE (vertical_code, insurer_code, canonical_name);


--
-- Name: product_releases uq_release_product_document; Type: CONSTRAINT; Schema: public; Owner: yangyajing
--

ALTER TABLE ONLY public.product_releases
    ADD CONSTRAINT uq_release_product_document UNIQUE (product_id, document_id);


--
-- Name: schema_versions uq_schema_vertical_version; Type: CONSTRAINT; Schema: public; Owner: yangyajing
--

ALTER TABLE ONLY public.schema_versions
    ADD CONSTRAINT uq_schema_vertical_version UNIQUE (vertical_code, version);


--
-- Name: verticals verticals_pkey; Type: CONSTRAINT; Schema: public; Owner: yangyajing
--

ALTER TABLE ONLY public.verticals
    ADD CONSTRAINT verticals_pkey PRIMARY KEY (code);


--
-- Name: documents documents_insurer_code_fkey; Type: FK CONSTRAINT; Schema: public; Owner: yangyajing
--

ALTER TABLE ONLY public.documents
    ADD CONSTRAINT documents_insurer_code_fkey FOREIGN KEY (insurer_code) REFERENCES public.insurers(code);


--
-- Name: extraction_runs extraction_runs_document_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: yangyajing
--

ALTER TABLE ONLY public.extraction_runs
    ADD CONSTRAINT extraction_runs_document_id_fkey FOREIGN KEY (document_id) REFERENCES public.documents(document_id);


--
-- Name: extraction_runs extraction_runs_schema_version_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: yangyajing
--

ALTER TABLE ONLY public.extraction_runs
    ADD CONSTRAINT extraction_runs_schema_version_id_fkey FOREIGN KEY (schema_version_id) REFERENCES public.schema_versions(schema_version_id);


--
-- Name: product_release_documents product_release_documents_document_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: yangyajing
--

ALTER TABLE ONLY public.product_release_documents
    ADD CONSTRAINT product_release_documents_document_id_fkey FOREIGN KEY (document_id) REFERENCES public.documents(document_id);


--
-- Name: product_release_documents product_release_documents_release_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: yangyajing
--

ALTER TABLE ONLY public.product_release_documents
    ADD CONSTRAINT product_release_documents_release_id_fkey FOREIGN KEY (release_id) REFERENCES public.product_releases(release_id);


--
-- Name: product_releases product_releases_document_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: yangyajing
--

ALTER TABLE ONLY public.product_releases
    ADD CONSTRAINT product_releases_document_id_fkey FOREIGN KEY (document_id) REFERENCES public.documents(document_id);


--
-- Name: product_releases product_releases_product_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: yangyajing
--

ALTER TABLE ONLY public.product_releases
    ADD CONSTRAINT product_releases_product_id_fkey FOREIGN KEY (product_id) REFERENCES public.products(product_id);


--
-- Name: product_releases product_releases_schema_version_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: yangyajing
--

ALTER TABLE ONLY public.product_releases
    ADD CONSTRAINT product_releases_schema_version_id_fkey FOREIGN KEY (schema_version_id) REFERENCES public.schema_versions(schema_version_id);


--
-- Name: products products_insurer_code_fkey; Type: FK CONSTRAINT; Schema: public; Owner: yangyajing
--

ALTER TABLE ONLY public.products
    ADD CONSTRAINT products_insurer_code_fkey FOREIGN KEY (insurer_code) REFERENCES public.insurers(code);


--
-- Name: products products_vertical_code_fkey; Type: FK CONSTRAINT; Schema: public; Owner: yangyajing
--

ALTER TABLE ONLY public.products
    ADD CONSTRAINT products_vertical_code_fkey FOREIGN KEY (vertical_code) REFERENCES public.verticals(code);


--
-- Name: raw_extractions raw_extractions_run_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: yangyajing
--

ALTER TABLE ONLY public.raw_extractions
    ADD CONSTRAINT raw_extractions_run_id_fkey FOREIGN KEY (run_id) REFERENCES public.extraction_runs(run_id);


--
-- Name: schema_versions schema_versions_vertical_code_fkey; Type: FK CONSTRAINT; Schema: public; Owner: yangyajing
--

ALTER TABLE ONLY public.schema_versions
    ADD CONSTRAINT schema_versions_vertical_code_fkey FOREIGN KEY (vertical_code) REFERENCES public.verticals(code);


--
-- Name: travel_product_details travel_product_details_release_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: yangyajing
--

ALTER TABLE ONLY public.travel_product_details
    ADD CONSTRAINT travel_product_details_release_id_fkey FOREIGN KEY (release_id) REFERENCES public.product_releases(release_id);


--
-- PostgreSQL database dump complete
--

\unrestrict oXJRoH1jMlA4Kd5C2d7PgkoMoMyeSvmgqQ9f2Tqk6L28bST7lo3vZQ3Q3OQDlf8

