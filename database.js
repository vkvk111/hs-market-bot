/**
 * Hero Siege Market Bot - Database Module
 * SQLite database for storing prices, materials, and recipes
 */

const Database = require('better-sqlite3');
const path = require('path');

// Database file path
const DB_PATH = path.join(__dirname, 'market.db');

// Initialize database
const db = new Database(DB_PATH);
db.pragma('journal_mode = WAL'); // Better performance

// Create tables
function initDatabase() {
    // Materials table - stores current prices for all materials
    db.exec(`
        CREATE TABLE IF NOT EXISTS materials (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            price INTEGER DEFAULT 0,
            ore TEXT,
            chance REAL,
            is_base INTEGER DEFAULT 0,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    `);

    // Ores table - stores ore costs
    db.exec(`
        CREATE TABLE IF NOT EXISTS ores (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            cost INTEGER DEFAULT 0,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    `);

    // Price history table - stores all price readings from bot
    db.exec(`
        CREATE TABLE IF NOT EXISTS price_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            item_name TEXT NOT NULL,
            price INTEGER NOT NULL,
            source TEXT DEFAULT 'bot',
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    `);

    // Recipes table
    db.exec(`
        CREATE TABLE IF NOT EXISTS recipes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            profit_mode TEXT DEFAULT '10%',
            custom_profit_value INTEGER DEFAULT 10000,
            custom_profit_type TEXT DEFAULT 'fixed',
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    `);

    // Recipe materials junction table
    db.exec(`
        CREATE TABLE IF NOT EXISTS recipe_materials (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            recipe_id INTEGER NOT NULL,
            material_name TEXT NOT NULL,
            quantity INTEGER NOT NULL,
            FOREIGN KEY (recipe_id) REFERENCES recipes(id),
            UNIQUE(recipe_id, material_name)
        )
    `);

    // Create indexes for faster queries
    db.exec(`
        CREATE INDEX IF NOT EXISTS idx_price_history_item ON price_history(item_name);
        CREATE INDEX IF NOT EXISTS idx_price_history_date ON price_history(created_at);
    `);

    console.log('Database initialized:', DB_PATH);
}

// Initialize tables BEFORE creating prepared statements
initDatabase();

// ============ ORES ============

const getOre = db.prepare('SELECT * FROM ores WHERE name = ?');
const getAllOres = db.prepare('SELECT * FROM ores ORDER BY name');
const upsertOre = db.prepare(`
    INSERT INTO ores (name, cost, updated_at) 
    VALUES (?, ?, CURRENT_TIMESTAMP)
    ON CONFLICT(name) DO UPDATE SET 
        cost = excluded.cost,
        updated_at = CURRENT_TIMESTAMP
`);

function setOreCost(name, cost) {
    return upsertOre.run(name, cost);
}

function getOreCost(name) {
    const ore = getOre.get(name);
    return ore ? ore.cost : 0;
}

function getOres() {
    return getAllOres.all();
}

// ============ MATERIALS ============

const getMaterial = db.prepare('SELECT * FROM materials WHERE name = ?');
const getAllMaterials = db.prepare('SELECT * FROM materials ORDER BY name');
const upsertMaterial = db.prepare(`
    INSERT INTO materials (name, price, ore, chance, is_base, updated_at) 
    VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
    ON CONFLICT(name) DO UPDATE SET 
        price = excluded.price,
        ore = COALESCE(excluded.ore, ore),
        chance = COALESCE(excluded.chance, chance),
        is_base = COALESCE(excluded.is_base, is_base),
        updated_at = CURRENT_TIMESTAMP
`);

function setMaterialPrice(name, price, ore = null, chance = null, isBase = 0) {
    return upsertMaterial.run(name, price, ore, chance, isBase);
}

function getMaterialPrice(name) {
    const mat = getMaterial.get(name);
    return mat ? mat.price : 0;
}

function getMaterials() {
    return getAllMaterials.all();
}

// ============ PRICE HISTORY ============

const insertPrice = db.prepare(`
    INSERT INTO price_history (item_name, price, source)
    VALUES (?, ?, ?)
`);

const getRecentPrices = db.prepare(`
    SELECT * FROM price_history 
    ORDER BY created_at DESC 
    LIMIT ?
`);

const getPricesForItem = db.prepare(`
    SELECT * FROM price_history 
    WHERE item_name = ? 
    ORDER BY created_at DESC 
    LIMIT ?
`);

const getAveragePrice = db.prepare(`
    SELECT item_name, 
           AVG(price) as avg_price,
           MIN(price) as min_price,
           MAX(price) as max_price,
           COUNT(*) as count
    FROM price_history 
    WHERE item_name = ?
    AND created_at > datetime('now', ?)
`);

const getPriceStats = db.prepare(`
    SELECT item_name, 
           AVG(price) as avg_price,
           MIN(price) as min_price,
           MAX(price) as max_price,
           COUNT(*) as count,
           MAX(created_at) as last_seen
    FROM price_history 
    WHERE created_at > datetime('now', ?)
    GROUP BY item_name
    ORDER BY count DESC
`);

function addPriceReading(itemName, price, source = 'bot') {
    const result = insertPrice.run(itemName, price, source);
    
    // Also update the current material price
    setMaterialPrice(itemName, price);
    
    return result;
}

function getRecentPriceHistory(limit = 100) {
    return getRecentPrices.all(limit);
}

function getItemPriceHistory(itemName, limit = 100) {
    return getPricesForItem.all(itemName, limit);
}

function getItemAveragePrice(itemName, timeRange = '-24 hours') {
    return getAveragePrice.get(itemName, timeRange);
}

function getAllPriceStats(timeRange = '-24 hours') {
    return getPriceStats.all(timeRange);
}

// ============ RECIPES ============

const getRecipe = db.prepare('SELECT * FROM recipes WHERE name = ?');
const getAllRecipes = db.prepare('SELECT * FROM recipes ORDER BY name');
const getRecipeMaterials = db.prepare(`
    SELECT material_name, quantity FROM recipe_materials WHERE recipe_id = ?
`);
const insertRecipe = db.prepare(`
    INSERT OR REPLACE INTO recipes (name, profit_mode, custom_profit_value, custom_profit_type, updated_at)
    VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
`);
const insertRecipeMaterial = db.prepare(`
    INSERT OR REPLACE INTO recipe_materials (recipe_id, material_name, quantity)
    VALUES (?, ?, ?)
`);
const deleteRecipeMaterials = db.prepare('DELETE FROM recipe_materials WHERE recipe_id = ?');

function saveRecipe(name, materials, profitMode = '10%', customProfitValue = 10000, customProfitType = 'fixed') {
    const transaction = db.transaction(() => {
        // Insert/update recipe
        insertRecipe.run(name, profitMode, customProfitValue, customProfitType);
        
        // Get recipe ID
        const recipe = getRecipe.get(name);
        
        // Clear existing materials and insert new ones
        deleteRecipeMaterials.run(recipe.id);
        for (const mat of materials) {
            insertRecipeMaterial.run(recipe.id, mat.name, mat.qty);
        }
        
        return recipe;
    });
    
    return transaction();
}

function loadRecipe(name) {
    const recipe = getRecipe.get(name);
    if (!recipe) return null;
    
    const materials = getRecipeMaterials.all(recipe.id);
    return {
        name: recipe.name,
        profitMode: recipe.profit_mode,
        customProfit: {
            value: recipe.custom_profit_value,
            type: recipe.custom_profit_type
        },
        materials: materials.map(m => ({ name: m.material_name, qty: m.quantity }))
    };
}

function loadAllRecipes() {
    const recipes = getAllRecipes.all();
    return recipes.map(r => loadRecipe(r.name));
}

function updateRecipeProfitSettings(name, profitMode, customProfitValue, customProfitType) {
    const stmt = db.prepare(`
        UPDATE recipes SET 
            profit_mode = ?,
            custom_profit_value = ?,
            custom_profit_type = ?,
            updated_at = CURRENT_TIMESTAMP
        WHERE name = ?
    `);
    return stmt.run(profitMode, customProfitValue, customProfitType, name);
}

// ============ BULK OPERATIONS ============

function saveAllPrices(ores, materials) {
    const transaction = db.transaction(() => {
        // Save ores
        for (const [name, cost] of Object.entries(ores)) {
            setOreCost(name, cost);
        }
        
        // Save materials
        for (const [name, data] of Object.entries(materials)) {
            setMaterialPrice(name, data.price, data.ore, data.chance, data.ore ? 0 : 1);
        }
    });
    
    return transaction();
}

function loadAllPrices() {
    const ores = {};
    const materials = {};
    
    for (const ore of getOres()) {
        ores[ore.name] = ore.cost;
    }
    
    for (const mat of getMaterials()) {
        materials[mat.name] = {
            price: mat.price,
            ore: mat.ore,
            chance: mat.chance,
            isBase: mat.is_base === 1
        };
    }
    
    return { ores, materials };
}

// ============ CLEANUP ============

function cleanOldPrices(daysToKeep = 30) {
    const stmt = db.prepare(`
        DELETE FROM price_history 
        WHERE created_at < datetime('now', ?)
    `);
    return stmt.run(`-${daysToKeep} days`);
}

module.exports = {
    db,
    // Ores
    setOreCost,
    getOreCost,
    getOres,
    // Materials
    setMaterialPrice,
    getMaterialPrice,
    getMaterials,
    // Price history
    addPriceReading,
    getRecentPriceHistory,
    getItemPriceHistory,
    getItemAveragePrice,
    getAllPriceStats,
    // Recipes
    saveRecipe,
    loadRecipe,
    loadAllRecipes,
    updateRecipeProfitSettings,
    // Bulk
    saveAllPrices,
    loadAllPrices,
    // Cleanup
    cleanOldPrices
};
