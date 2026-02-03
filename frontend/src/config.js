// Centralized configuration for the API URL
// In Vercel, set VITE_API_URL environment variable to your Ngrok/Backend URL without trailing slash
// Example: https://1234-56-78.ngrok-free.app
export const API_BASE_URL = import.meta.env.VITE_API_URL || 'http://localhost:8001/api'; 
