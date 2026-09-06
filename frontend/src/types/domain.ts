/**
 * Hand-mirror of backend/app/models/domain.py.
 *
 * Field names stay snake_case on purpose: these objects travel the wire unchanged, and
 * a camelCase mirror would need a mapping layer on every read and write. If you add a
 * field to the Python model, add it here too.
 */

export type TravelMode = 'driving' | 'walking' | 'straight'
export type CostModel = 'haversine' | 'amap'
export type PlaceStatus = 'confirmed' | 'pending'

export interface Trip {
  id: string
  title: string
  city: string
  travel_mode: TravelMode
  cost_model: CostModel
  day_start_min: number
  seq: number
  created_at: string
}

export interface Day {
  id: string
  trip_id: string
  day_index: number
  date: string | null
  title: string
  start_place_id: string | null
  end_place_id: string | null
  start_min: number | null
  travel_mode: TravelMode | null
  rev: number
}

export interface Place {
  id: string
  day_id: string
  trip_id: string
  sort_index: number
  name: string
  amap_poi_id: string
  address: string
  lng: number
  lat: number
  duration_min: number
  start_min: number | null
  arrive_min: number | null
  travel_min_before: number | null
  locked: boolean
  status: PlaceStatus
  note: string
  added_by: string
  rev: number
  created_at: string
  updated_at: string
}

export interface Participant {
  trip_id: string
  client_id: string
  name: string
  color: string
  joined_at: string
  last_seen: string
}

/** Who is online right now. Server-side this is in-memory only, never persisted. */
export interface Presence {
  client_id: string
  name: string
  color: string
  current_day_id: string | null
  focusing_place_id: string | null
  dragging_day_id?: string | null
  joined_at: number
}

export interface Snapshot {
  trip: Trip
  days: Day[]
  places: Place[]
  participants: Participant[]
  presence: Presence[]
}

/** A POI from the backend proxy. Coordinates are GCJ-02 -- never convert them. */
export interface Poi {
  id: string
  name: string
  address: string
  lng: number
  lat: number
  city: string
  district: string
}

export interface TripCreateResult {
  trip_id: string
  day_id: string
  share_url: string
}

export interface PlaceCreateInput {
  name: string
  lng: number
  lat: number
  address?: string
  amap_poi_id?: string
  duration_min?: number
  note?: string
  added_by?: string
  after_place_id?: string | null
}
