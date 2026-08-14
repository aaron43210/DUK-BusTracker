import { decodePolyline6 } from './polyline';

/**
 * Fetches an optimized route or standard route from local Valhalla server.
 * @param {Array} coordinates Array of [lon, lat] points to route through
 */
export async function fetchValhallaRoute(coordinates) {
    if (!coordinates || coordinates.length < 2) return [];

    const locations = coordinates.map(coord => ({
        lat: coord[1],
        lon: coord[0]
    }));

    const requestBody = {
        locations: locations,
        costing: "auto",
        directions_options: { units: "kilometers" }
    };

    try {
        const response = await fetch('http://localhost:8002/route', {
            method: 'POST',
            body: JSON.stringify(requestBody),
            headers: { 'Content-Type': 'application/json' }
        });
        
        if (!response.ok) {
            console.error("Valhalla API returned error:", response.status);
            return [];
        }

        const data = await response.json();
        
        let allDecodedCoords = [];
        
        // Decode the shape for all legs
        if (data && data.trip && data.trip.legs) {
            data.trip.legs.forEach(leg => {
                const decodedCoords = decodePolyline6(leg.shape);
                allDecodedCoords = allDecodedCoords.concat(decodedCoords);
            });
        }
        
        return allDecodedCoords;
    } catch (error) {
        console.error("Valhalla routing error:", error);
        return [];
    }
}
