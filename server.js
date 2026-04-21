const express = require('express');
const path = require('path');
const http = require('http');
const WebSocket = require('ws');
const db = require('./database');

const app = express();
const PORT = 8080;

// Create HTTP server
const server = http.createServer(app);

// Create WebSocket server
const wss = new WebSocket.Server({ server, path: '/ws' });

// Store connected clients
const clients = {
    pythonClient: null,
    webClients: new Set()
};

// Client state
let clientState = {
    state: 'disconnected',
    stats: {
        uptime: 0,
        prices_read: 0,
        prices_sent: 0,
        errors: 0,
        last_read_time: 0,
        last_send_time: 0
    }
};

// Session stats (resets when server restarts)
let sessionStats = {
    startTime: Date.now(),
    bought: [],      // Array of { item, quantity, unit_price, total, timestamp }
    sold: [],        // Array of { item, quantity, unit_price, total, timestamp }
    lowestPrices: {}, // { item_name: { price, timestamp } }
    marketPrices: {}  // { item_name: { prices: [{price, quantity}], lowest, timestamp } }
};

// Previous listings for detecting sold items
let previousListings = null;

// Recently canceled listings (to exclude from sold detection)
let recentlyCanceled = [];  // Array of { item, unit_price, quantity, timestamp }

// Track total sold quantities per item (for stock tracking)
let soldCounts = {};  // { item_name: total_quantity_sold }

// Track buy cycle results for auto-pricing
// { item_name: { consecutiveBuys: n, cyclesWithoutBuy: n } }
let buyCycleTracking = {};

// Price history (in-memory cache, backed by DB)
let priceHistory = [];
const MAX_HISTORY = 1000;

// Load recent prices from database on startup
try {
    const dbPrices = db.getRecentPriceHistory(MAX_HISTORY);
    priceHistory = dbPrices.map(p => ({
        item_name: p.item_name,
        price: p.price,
        timestamp: new Date(p.created_at).getTime() / 1000,
        received_at: new Date(p.created_at).getTime()
    })).reverse();
    console.log(`Loaded ${priceHistory.length} prices from database`);
} catch (e) {
    console.error('Error loading prices from DB:', e);
}

// WebSocket connection handler
wss.on('connection', (ws, req) => {
    const clientType = req.headers['x-client-type'] || 'web';
    
    console.log(`WebSocket connected: ${clientType}`);
    
    if (clientType === 'python') {
        clients.pythonClient = ws;
        clientState.state = 'connected';
        broadcastToWeb({ type: 'client_connected' });
    } else {
        clients.webClients.add(ws);
        // Send current state to new web client
        ws.send(JSON.stringify({
            type: 'init',
            state: clientState,
            recentPrices: priceHistory.slice(-50)
        }));
    }
    
    ws.on('message', (data) => {
        try {
            const message = JSON.parse(data.toString());
            handleMessage(ws, message, clientType);
        } catch (e) {
            console.error('Invalid message:', e);
        }
    });
    
    ws.on('close', () => {
        console.log(`WebSocket disconnected: ${clientType}`);
        
        if (clientType === 'python') {
            clients.pythonClient = null;
            clientState.state = 'disconnected';
            broadcastToWeb({ type: 'client_disconnected' });
        } else {
            clients.webClients.delete(ws);
        }
    });
    
    ws.on('error', (err) => {
        console.error('WebSocket error:', err);
    });
});

// Handle incoming messages
function handleMessage(ws, message, clientType) {
    const { type } = message;
    
    if (clientType === 'python' || clientType === 'web') {
        switch (type) {
            case 'status':
                // Python client sending status update
                clientState.state = message.state;
                clientState.stats = message.stats;
                broadcastToWeb({ type: 'status', ...clientState });
                break;
            
            case 'item_bought':
                // Python client bought an item
                const buyData = {
                    item: message.item,
                    quantity: message.quantity || 1,
                    unit_price: message.unit_price,
                    total: message.total || message.unit_price * (message.quantity || 1),
                    timestamp: Date.now()
                };
                sessionStats.bought.push(buyData);
                // Track buy counts for max per session limit
                const buyItemName = (message.item || '').toLowerCase().trim();
                if (buyItemName) {
                    boughtCounts[buyItemName] = (boughtCounts[buyItemName] || 0) + (message.quantity || 1);
                }
                // Track "buy under avg" session count
                if (message.buyUnderAvg) {
                    buyUnderAvgSessionCount += (message.quantity || 1);
                    console.log(`📉 BUY UNDER AVG: ${buyData.quantity}x ${buyData.item} (session count: ${buyUnderAvgSessionCount})`);
                }
                // Update persistent totalBought counter
                updateTotalBought(message.item, message.quantity || 1);
                console.log(`📦 BOUGHT: ${buyData.quantity}x ${buyData.item} @ ${buyData.unit_price} each (total: ${buyData.total})`);
                broadcastToWeb({ type: 'item_bought', data: buyData, stats: getSessionStatsSummary() });
                break;
            
            case 'item_sold':
                // Python client sold an item
                const sellData = {
                    item: message.item,
                    quantity: message.quantity || 1,
                    unit_price: message.unit_price,
                    total: message.total || message.unit_price * (message.quantity || 1),
                    timestamp: Date.now()
                };
                sessionStats.sold.push(sellData);
                console.log(`💰 SOLD: ${sellData.quantity}x ${sellData.item} @ ${sellData.unit_price} each (total: ${sellData.total})`);
                broadcastToWeb({ type: 'item_sold', data: sellData, stats: getSessionStatsSummary() });
                // Note: Stock is decremented on item_listed, not item_sold
                break;
            
            case 'item_listed':
                // Python client listed an item for sale - decrement stock now
                const listedData = {
                    item: message.item,
                    quantity: message.quantity || 1,
                    unit_price: message.price || 0,
                    timestamp: Date.now()
                };
                console.log(`📋 LISTED: ${listedData.quantity}x ${listedData.item} @ ${listedData.unit_price}`);
                // Check stock and auto-disable if depleted
                checkAndDisableOutOfStock(listedData.item, listedData.quantity);
                broadcastToWeb({ type: 'item_listed', data: listedData, stats: getSessionStatsSummary() });
                break;
            
            case 'listing_canceled':
                // Python client canceled a listing (don't count as sold)
                const cancelData = {
                    item: message.item,
                    quantity: message.quantity || 1,
                    unit_price: message.unit_price || 0,
                    timestamp: Date.now()
                };
                recentlyCanceled.push(cancelData);
                console.log(`🔄 CANCELED: ${cancelData.quantity}x ${cancelData.item} @ ${cancelData.unit_price} (will not count as sold)`);
                // Note: Stock is NOT restored - once listed, it's counted as used from stock
                // Clean up old cancels (older than 30 seconds)
                const thirtySecondsAgo = Date.now() - 30000;
                recentlyCanceled = recentlyCanceled.filter(c => c.timestamp > thirtySecondsAgo);
                break;
            
            case 'market_prices':
                // Python client reporting all market prices for an item
                const itemNameMp = message.item;
                const allPrices = message.prices || [];
                const lowestPrice = message.lowest;
                
                // Store full price distribution
                sessionStats.marketPrices[itemNameMp] = {
                    prices: allPrices,
                    lowest: lowestPrice,
                    timestamp: Date.now()
                };
                
                // Save lowest price to database for historical tracking
                try {
                    db.addPriceReading(itemNameMp, lowestPrice, 'bot');
                } catch (e) {
                    console.error('Error saving price to DB:', e);
                }
                
                // Also update lowestPrices for backward compat
                const prevMp = sessionStats.lowestPrices[itemNameMp];
                if (!prevMp || lowestPrice < prevMp.price) {
                    sessionStats.lowestPrices[itemNameMp] = { price: lowestPrice, timestamp: Date.now() };
                    console.log(`📊 New lowest for ${itemNameMp}: ${lowestPrice}`);
                }
                
                broadcastToWeb({ type: 'market_prices', item: itemNameMp, prices: allPrices, lowest: lowestPrice, stats: getSessionStatsSummary() });
                break;
            
            case 'lowest_price':
                // Python client reporting lowest price for an item (legacy)
                const itemName = message.item;
                const price = message.price;
                const prev = sessionStats.lowestPrices[itemName];
                if (!prev || price < prev.price) {
                    sessionStats.lowestPrices[itemName] = { price, timestamp: Date.now() };
                    console.log(`📊 New lowest for ${itemName}: ${price}`);
                }
                broadcastToWeb({ type: 'lowest_price', item: itemName, price, stats: getSessionStatsSummary() });
                break;
                
            case 'price_update':
                // Python client sending price data
                const priceData = {
                    ...message.data,
                    received_at: Date.now()
                };
                
                // Save to database
                try {
                    db.addPriceReading(priceData.item_name, priceData.price, 'bot');
                } catch (e) {
                    console.error('Error saving price to DB:', e);
                }
                
                // Add to in-memory cache
                priceHistory.push(priceData);
                
                // Trim history
                if (priceHistory.length > MAX_HISTORY) {
                    priceHistory.shift();
                }
                
                // Broadcast to web clients
                broadcastToWeb({ type: 'price_update', data: priceData });
                
                console.log(`Price: ${priceData.item_name} = ${priceData.price} (saved to DB)`);
                break;
                
            case 'command':
                // Web client sending command to Python client
                if (clients.pythonClient && clients.pythonClient.readyState === WebSocket.OPEN) {
                    clients.pythonClient.send(JSON.stringify(message));
                    console.log(`Command sent to client: ${message.command}`);
                } else {
                    ws.send(JSON.stringify({
                        type: 'error',
                        message: 'Python client not connected'
                    }));
                }
                break;
            
            case 'listings_update':
                // Python client sending my listings data
                const currentListings = message.listings || [];
                console.log(`Received ${currentListings.length} listings from bot`);
                
                // Store current listings count for session stats
                sessionStats.currentListingsCount = currentListings.length;
                sessionStats.currentListingsValue = currentListings.reduce((sum, l) => sum + (l.price || 0), 0);
                
                // Detect sold items by comparing with previous listings
                if (previousListings !== null) {
                    // Helper to normalize name for consistent matching
                    const normalizeName = (name) => (name || 'Unknown').toLowerCase().trim().replace(/\s+/g, ' ');
                    
                    // Create a map of current listings by unique key (normalized name + unit_price)
                    const currentMap = new Map();
                    currentListings.forEach(listing => {
                        const unitPrice = listing.unit_price || listing.price || 0;
                        const key = `${normalizeName(listing.name)}|${unitPrice}`;
                        const existing = currentMap.get(key) || 0;
                        currentMap.set(key, existing + (listing.quantity || 1));
                    });
                    
                    // Create a map of previous listings
                    const previousMap = new Map();
                    previousListings.forEach(listing => {
                        const unitPrice = listing.unit_price || listing.price || 0;
                        const key = `${normalizeName(listing.name)}|${unitPrice}`;
                        const existing = previousMap.get(key) || 0;
                        previousMap.set(key, existing + (listing.quantity || 1));
                    });
                    
                    // Find items that decreased in quantity or disappeared
                    previousMap.forEach((prevQty, key) => {
                        const currQty = currentMap.get(key) || 0;
                        let soldQty = prevQty - currQty;
                        
                        if (soldQty > 0) {
                            const [name, priceStr] = key.split('|');
                            const unit_price = parseInt(priceStr) || 0;
                            
                            // Check if this item was recently canceled (not actually sold)
                            const canceledMatch = recentlyCanceled.find(c => 
                                c.item.toLowerCase() === name.toLowerCase() && 
                                (c.unit_price === 0 || c.unit_price === unit_price)
                            );
                            
                            if (canceledMatch) {
                                // Subtract the canceled quantity from the sold quantity
                                const canceledQty = canceledMatch.quantity || 1;
                                soldQty -= canceledQty;
                                console.log(`📋 Excluded ${canceledQty}x ${name} from sold (was canceled, not sold)`);
                                // Remove from recentlyCanceled so it's only used once
                                recentlyCanceled = recentlyCanceled.filter(c => c !== canceledMatch);
                            }
                            
                            // Only record if there's still a positive sold quantity
                            if (soldQty > 0) {
                                const sellData = {
                                    item: name,
                                    quantity: soldQty,
                                    unit_price: unit_price,
                                    total: unit_price * soldQty,
                                    timestamp: Date.now()
                                };
                                sessionStats.sold.push(sellData);
                                console.log(`💰 SOLD: ${sellData.quantity}x ${sellData.item} @ ${sellData.unit_price} each (total: ${sellData.total})`);
                                broadcastToWeb({ type: 'item_sold', data: sellData, stats: getSessionStatsSummary() });
                                // Note: Stock was already decremented when item was listed, not here
                            }
                        }
                    });
                }
                
                // Store current listings for next comparison
                previousListings = currentListings;
                
                broadcastToWeb({ type: 'listings_update', listings: currentListings, stats: getSessionStatsSummary() });
                break;
        }
    }
}

// Get session stats summary
function getSessionStatsSummary() {
    const totalBought = sessionStats.bought.reduce((sum, b) => sum + b.total, 0);
    const totalSold = sessionStats.sold.reduce((sum, s) => sum + s.total, 0);
    const itemsBought = sessionStats.bought.reduce((sum, b) => sum + (b.quantity || 1), 0);
    const itemsSold = sessionStats.sold.reduce((sum, s) => sum + (s.quantity || 1), 0);
    
    // Group by item
    const boughtByItem = {};
    sessionStats.bought.forEach(b => {
        if (!boughtByItem[b.item]) boughtByItem[b.item] = { count: 0, total: 0 };
        boughtByItem[b.item].count += b.quantity;
        boughtByItem[b.item].total += b.total;
    });
    
    const soldByItem = {};
    sessionStats.sold.forEach(s => {
        if (!soldByItem[s.item]) soldByItem[s.item] = { count: 0, total: 0 };
        soldByItem[s.item].count += s.quantity;
        soldByItem[s.item].total += s.total;
    });
    
    // Get hourly price averages from database
    let hourlyPriceStats = {};
    try {
        const stats = db.getAllPriceStats('-1 hour');
        stats.forEach(s => {
            hourlyPriceStats[s.item_name] = {
                avg: Math.round(s.avg_price),
                min: s.min_price,
                max: s.max_price,
                count: s.count
            };
        });
    } catch (e) {
        console.error('Error getting hourly stats:', e);
    }
    
    return {
        startTime: sessionStats.startTime,
        uptime: Date.now() - sessionStats.startTime,
        itemsBought,
        totalBought,
        boughtByItem,
        itemsSold,
        totalSold,
        soldByItem,
        soldCounts,  // Include sold counts for stock tracking
        lowestPrices: sessionStats.lowestPrices,
        marketPrices: sessionStats.marketPrices || {},
        hourlyPriceStats,
        recentBought: sessionStats.bought.slice(-10),
        recentSold: sessionStats.sold.slice(-10),
        currentListingsCount: sessionStats.currentListingsCount || 0,
        currentListingsValue: sessionStats.currentListingsValue || 0
    };
}

// Check stock and auto-disable items when stock is depleted
function checkAndDisableOutOfStock(itemName, quantitySold) {
    const fs = require('fs');
    const file = path.join(__dirname, 'public', 'sell_config.json');
    
    // Update sold count
    const normalizedName = itemName.toLowerCase().trim();
    if (!soldCounts[normalizedName]) {
        soldCounts[normalizedName] = 0;
    }
    soldCounts[normalizedName] += quantitySold;
    
    // Read sell config and check stock
    try {
        const data = fs.readFileSync(file, 'utf8');
        const config = JSON.parse(data);
        
        // Find matching item (case-insensitive)
        let matchedKey = null;
        for (const key of Object.keys(config.items || {})) {
            if (key.toLowerCase().trim() === normalizedName) {
                matchedKey = key;
                break;
            }
        }
        
        if (!matchedKey) return;
        
        const itemConfig = config.items[matchedKey];
        const stock = itemConfig.stock || 10;  // Default stock is 10
        const totalSold = soldCounts[normalizedName];
        const remaining = stock - totalSold;
        
        console.log(`📦 Stock check: ${matchedKey} - sold ${totalSold}/${stock} (${remaining} remaining)`);
        
        if (remaining <= 0 && itemConfig.enabled) {
            // Out of stock - disable the item
            itemConfig.enabled = false;
            itemConfig.staleRelist = false;  // Also disable stale relist
            
            fs.writeFileSync(file, JSON.stringify(config, null, 2));
            console.log(`⛔ OUT OF STOCK: ${matchedKey} disabled (sold ${totalSold}/${stock})`);
            
            // Broadcast update to web clients
            broadcastToWeb({ 
                type: 'stock_depleted', 
                item: matchedKey, 
                sold: totalSold, 
                stock: stock 
            });
        }
    } catch (e) {
        console.error('Error checking stock:', e);
    }
}

// Broadcast message to all web clients
function broadcastToWeb(message) {
    const data = JSON.stringify(message);
    clients.webClients.forEach(client => {
        if (client.readyState === WebSocket.OPEN) {
            client.send(data);
        }
    });
}

// Middleware
app.use(express.json());
app.use(express.static(path.join(__dirname, 'public')));

// Serve the main page
app.get('/', (req, res) => {
    res.sendFile(path.join(__dirname, 'public', 'index.html'));
});

// Serve control panel
app.get('/control', (req, res) => {
    res.sendFile(path.join(__dirname, 'public', 'control.html'));
});

// API: Get current state
app.get('/api/state', (req, res) => {
    res.json({
        clientConnected: clients.pythonClient !== null,
        state: clientState,
        webClients: clients.webClients.size
    });
});

// API: Get price history
app.get('/api/prices', (req, res) => {
    const limit = parseInt(req.query.limit) || 100;
    res.json(priceHistory.slice(-limit));
});

// API: Get price history from database
app.get('/api/prices/history', (req, res) => {
    const limit = parseInt(req.query.limit) || 100;
    const item = req.query.item;
    
    try {
        if (item) {
            const prices = db.getItemPriceHistory(item, limit);
            res.json(prices);
        } else {
            const prices = db.getRecentPriceHistory(limit);
            res.json(prices);
        }
    } catch (e) {
        res.status(500).json({ error: e.message });
    }
});

// API: Get price statistics
app.get('/api/prices/stats', (req, res) => {
    const timeRange = req.query.range || '-24 hours';
    
    try {
        const stats = db.getAllPriceStats(timeRange);
        res.json(stats);
    } catch (e) {
        res.status(500).json({ error: e.message });
    }
});

// API: Get 1h average for an item (for auto-buy logic)
app.get('/api/prices/average/:item', (req, res) => {
    const item = req.params.item;
    const timeRange = req.query.range || '-1 hours';
    
    try {
        const stats = db.getItemAveragePrice(item, timeRange);
        res.json({
            item,
            avg_price: stats?.avg_price || null,
            min_price: stats?.min_price || null,
            max_price: stats?.max_price || null,
            count: stats?.count || 0,
            timeRange
        });
    } catch (e) {
        res.status(500).json({ error: e.message });
    }
});

// API: Get 1h averages for all enabled buy items
app.get('/api/prices/averages', (req, res) => {
    const fs = require('fs');
    const configFile = path.join(__dirname, 'public', 'buy_config.json');
    const timeRange = req.query.range || '-1 hours';
    
    fs.readFile(configFile, 'utf8', (err, data) => {
        if (err) return res.status(500).json({ error: err.message });
        
        try {
            const config = JSON.parse(data);
            const averages = {};
            
            for (const [name, item] of Object.entries(config.items || {})) {
                if (item.enabled && !item.hidden) {
                    const stats = db.getItemAveragePrice(name, timeRange);
                    averages[name] = {
                        avg_price: stats?.avg_price ? Math.round(stats.avg_price) : null,
                        min_price: stats?.min_price || null,
                        max_price: stats?.max_price || null,
                        count: stats?.count || 0
                    };
                }
            }
            
            res.json({ averages, timeRange });
        } catch (e) {
            res.status(500).json({ error: e.message });
        }
    });
});

// API: Get item average price
app.get('/api/prices/average/:item', (req, res) => {
    const item = req.params.item;
    const timeRange = req.query.range || '-24 hours';
    
    try {
        const avg = db.getItemAveragePrice(item, timeRange);
        res.json(avg);
    } catch (e) {
        res.status(500).json({ error: e.message });
    }
});

// API: Save all prices (ores + materials)
app.post('/api/prices/save', (req, res) => {
    const { ores, materials } = req.body;
    
    try {
        db.saveAllPrices(ores, materials);
        res.json({ success: true, message: 'Prices saved to database' });
    } catch (e) {
        res.status(500).json({ error: e.message });
    }
});

// API: Load all prices from database
app.get('/api/prices/load', (req, res) => {
    try {
        const data = db.loadAllPrices();
        res.json(data);
    } catch (e) {
        res.status(500).json({ error: e.message });
    }
});

// API: Update single ore cost
app.post('/api/ores/:name', (req, res) => {
    const { name } = req.params;
    const { cost } = req.body;
    
    try {
        db.setOreCost(name, cost);
        res.json({ success: true });
    } catch (e) {
        res.status(500).json({ error: e.message });
    }
});

// API: Update single material price
app.post('/api/materials/:name', (req, res) => {
    const { name } = req.params;
    const { price, ore, chance, isBase } = req.body;
    
    try {
        db.setMaterialPrice(name, price, ore, chance, isBase ? 1 : 0);
        res.json({ success: true });
    } catch (e) {
        res.status(500).json({ error: e.message });
    }
});

// API: Save recipe
app.post('/api/recipes', (req, res) => {
    const { name, materials, profitMode, customProfitValue, customProfitType } = req.body;
    
    try {
        db.saveRecipe(name, materials, profitMode, customProfitValue, customProfitType);
        res.json({ success: true });
    } catch (e) {
        res.status(500).json({ error: e.message });
    }
});

// API: Load all recipes
app.get('/api/recipes', (req, res) => {
    try {
        const recipes = db.loadAllRecipes();
        res.json(recipes);
    } catch (e) {
        res.status(500).json({ error: e.message });
    }
});

// API: Update recipe profit settings
app.put('/api/recipes/:name/profit', (req, res) => {
    const { name } = req.params;
    const { profitMode, customProfitValue, customProfitType } = req.body;
    
    try {
        db.updateRecipeProfitSettings(name, profitMode, customProfitValue, customProfitType);
        res.json({ success: true });
    } catch (e) {
        res.status(500).json({ error: e.message });
    }
});

// API endpoint for calculations (for future DB integration)
app.post('/api/calculate', (req, res) => {
    const { materials, sellPrice } = req.body;
    
    const totalCost = materials.reduce((sum, mat) => sum + (mat.cost * mat.quantity), 0);
    const profit = sellPrice - totalCost;
    const profitMargin = sellPrice > 0 ? ((profit / sellPrice) * 100).toFixed(2) : 0;
    const worthCrafting = profit > 0;
    
    res.json({
        totalCost,
        profit,
        profitMargin,
        worthCrafting
    });
});

// API: Get buy thresholds
// API: Get buy thresholds (for Python client - returns only enabled items as simple object)
app.get('/api/buy_thresholds', (req, res) => {
    const fs = require('fs');
    const file = path.join(__dirname, 'public', 'buy_config.json');
    fs.readFile(file, 'utf8', (err, data) => {
        if (err) return res.status(500).json({ error: err.message });
        try {
            const config = JSON.parse(data);
            // Return simple {name: threshold} for enabled items (including threshold=0 for price tracking)
            const thresholds = {};
            for (const [name, item] of Object.entries(config.items || {})) {
                if (item.enabled && !item.hidden) {
                    thresholds[name] = item.threshold || 0;
                }
            }
            res.json(thresholds);
        } catch (e) {
            res.status(500).json({ error: e.message });
        }
    });
});

// API: Get full buy config (for web UI)
app.get('/api/buy_config', (req, res) => {
    const fs = require('fs');
    const file = path.join(__dirname, 'public', 'buy_config.json');
    fs.readFile(file, 'utf8', (err, data) => {
        if (err) return res.status(500).json({ error: err.message });
        try {
            res.json(JSON.parse(data));
        } catch (e) {
            res.status(500).json({ error: e.message });
        }
    });
});

// API: Save buy config
app.post('/api/buy_config', (req, res) => {
    const fs = require('fs');
    const file = path.join(__dirname, 'public', 'buy_config.json');
    fs.writeFile(file, JSON.stringify(req.body, null, 2), err => {
        if (err) return res.status(500).json({ success: false, error: err.message });
        res.json({ success: true });
    });
});

// API: Add new item to config
app.post('/api/buy_config/add', (req, res) => {
    const fs = require('fs');
    const file = path.join(__dirname, 'public', 'buy_config.json');
    const { name } = req.body;
    if (!name) return res.status(400).json({ success: false, error: 'Name required' });
    
    fs.readFile(file, 'utf8', (err, data) => {
        if (err) return res.status(500).json({ success: false, error: err.message });
        try {
            const config = JSON.parse(data);
            const normalizedName = name.toLowerCase().trim();
            if (!config.items[normalizedName]) {
                config.items[normalizedName] = { threshold: 0, enabled: false, hidden: false };
            }
            fs.writeFile(file, JSON.stringify(config, null, 2), err => {
                if (err) return res.status(500).json({ success: false, error: err.message });
                res.json({ success: true, config });
            });
        } catch (e) {
            res.status(500).json({ success: false, error: e.message });
        }
    });
});

// Track buy counts per session (for max per session limit)
let boughtCounts = {};  // { item_name: count }
let buyUnderAvgSessionCount = 0;  // Track "buy under avg" buys per session

// API: Get buy options
app.get('/api/buy_options', (req, res) => {
    const fs = require('fs');
    const file = path.join(__dirname, 'public', 'buy_options.json');
    fs.readFile(file, 'utf8', (err, data) => {
        if (err) return res.status(500).json({ error: err.message });
        try {
            const config = JSON.parse(data);
            // Include boughtCounts so client knows remaining buys
            config.boughtCounts = boughtCounts;
            config.buyUnderAvgSessionCount = buyUnderAvgSessionCount;
            res.json(config);
        } catch (e) {
            res.status(500).json({ error: e.message });
        }
    });
});

// API: Save buy options
app.post('/api/buy_options', (req, res) => {
    const fs = require('fs');
    const file = path.join(__dirname, 'public', 'buy_options.json');
    // Don't save boughtCounts to file
    const { boughtCounts: _, ...configToSave } = req.body;
    fs.writeFile(file, JSON.stringify(configToSave, null, 2), err => {
        if (err) return res.status(500).json({ success: false, error: err.message });
        res.json({ success: true });
    });
});

// API: Reset buy counts
app.post('/api/buy_counts/reset', (req, res) => {
    const { item } = req.body;
    if (item) {
        const normalizedName = item.toLowerCase().trim();
        delete boughtCounts[normalizedName];
        console.log(`🛒 Reset buy count for: ${item}`);
    } else {
        boughtCounts = {};
        buyUnderAvgSessionCount = 0;
        console.log('🛒 Reset all buy counts');
    }
    res.json({ success: true, boughtCounts, buyUnderAvgSessionCount });
});

// Update persistent totalBought counter in buy_options.json
function updateTotalBought(itemName, quantity) {
    const fs = require('fs');
    const file = path.join(__dirname, 'public', 'buy_options.json');
    try {
        const data = fs.readFileSync(file, 'utf8');
        const config = JSON.parse(data);
        if (!config.items) config.items = {};
        if (!config.items[itemName]) config.items[itemName] = {};
        config.items[itemName].totalBought = (config.items[itemName].totalBought || 0) + quantity;
        fs.writeFileSync(file, JSON.stringify(config, null, 2));
        console.log(`🎯 ${itemName}: totalBought = ${config.items[itemName].totalBought}`);
    } catch (e) {
        console.error('Failed to update totalBought:', e.message);
    }
}

// API: Reset totalBought for an item
app.post('/api/buy_total/reset', (req, res) => {
    const fs = require('fs');
    const file = path.join(__dirname, 'public', 'buy_options.json');
    const { item } = req.body;
    try {
        const data = fs.readFileSync(file, 'utf8');
        const config = JSON.parse(data);
        if (item && config.items && config.items[item]) {
            config.items[item].totalBought = 0;
            console.log(`🎯 Reset totalBought for: ${item}`);
        } else if (!item && config.items) {
            // Reset all
            for (const name of Object.keys(config.items)) {
                config.items[name].totalBought = 0;
            }
            console.log('🎯 Reset all totalBought counters');
        }
        fs.writeFileSync(file, JSON.stringify(config, null, 2));
        res.json({ success: true });
    } catch (e) {
        res.status(500).json({ success: false, error: e.message });
    }
});

// API: Report buy cycle result and auto-adjust price
app.post('/api/buy_cycle_result', (req, res) => {
    const fs = require('fs');
    const { item, bought, currentPrice } = req.body;
    if (!item) return res.status(400).json({ success: false, error: 'Item required' });
    
    const normalizedName = item.toLowerCase().trim();
    
    // Initialize tracking if needed
    if (!buyCycleTracking[normalizedName]) {
        buyCycleTracking[normalizedName] = { consecutiveBuys: 0, cyclesWithoutBuy: 0 };
    }
    
    const tracking = buyCycleTracking[normalizedName];
    
    if (bought) {
        tracking.consecutiveBuys++;
        tracking.cyclesWithoutBuy = 0;
    } else {
        tracking.cyclesWithoutBuy++;
        tracking.consecutiveBuys = 0;
    }
    
    // Load buy options to check auto-pricing settings
    const optionsFile = path.join(__dirname, 'public', 'buy_options.json');
    const configFile = path.join(__dirname, 'public', 'buy_config.json');
    
    fs.readFile(optionsFile, 'utf8', (err, optionsData) => {
        if (err) return res.json({ success: true, tracking, adjusted: false });
        
        try {
            const options = JSON.parse(optionsData);
            const itemOpts = options.items?.[item] || {};
            
            // Check if auto-pricing is enabled for this item
            if (!itemOpts.autoPricing) {
                return res.json({ success: true, tracking, adjusted: false });
            }
            
            const cyclesToIncrease = itemOpts.cyclesToIncrease || 3;
            const cyclesToDecrease = itemOpts.cyclesToDecrease || 3;
            const increasePercent = itemOpts.increasePercent || 5;
            const decreasePercent = itemOpts.decreasePercent || 5;
            const minThreshold = itemOpts.minThreshold || 1000;
            // maxThreshold: 0 or undefined = no limit (use very large number)
            const maxThreshold = itemOpts.maxThreshold > 0 ? itemOpts.maxThreshold : 999999999;
            
            let priceAdjustment = 0;
            let reason = null;
            
            // Check if we should adjust price
            if (tracking.cyclesWithoutBuy >= cyclesToIncrease && currentPrice > 0) {
                // Increase price (haven't been able to buy)
                priceAdjustment = Math.round(currentPrice * (increasePercent / 100));
                reason = `No buy for ${tracking.cyclesWithoutBuy} cycles`;
            } else if (tracking.consecutiveBuys >= cyclesToDecrease && currentPrice > 0) {
                // Decrease price (buying successfully, can afford to lower threshold)
                priceAdjustment = -Math.round(currentPrice * (decreasePercent / 100));
                reason = `Bought ${tracking.consecutiveBuys} cycles in a row`;
            }
            
            if (priceAdjustment === 0) {
                return res.json({ success: true, tracking, adjusted: false });
            }
            
            // Apply price adjustment to buy_config
            fs.readFile(configFile, 'utf8', (err, configData) => {
                if (err) return res.json({ success: true, tracking, adjusted: false });
                
                try {
                    const config = JSON.parse(configData);
                    if (!config.items?.[item]) {
                        return res.json({ success: true, tracking, adjusted: false });
                    }
                    
                    const oldPrice = config.items[item].threshold || 0;
                    let newPrice = oldPrice + priceAdjustment;
                    
                    // Clamp to min/max
                    newPrice = Math.max(minThreshold, Math.min(maxThreshold, newPrice));
                    
                    if (newPrice === oldPrice) {
                        return res.json({ success: true, tracking, adjusted: false });
                    }
                    
                    config.items[item].threshold = newPrice;
                    
                    fs.writeFile(configFile, JSON.stringify(config, null, 2), (err) => {
                        if (err) return res.json({ success: true, tracking, adjusted: false });
                        
                        // Reset tracking after adjustment
                        tracking.consecutiveBuys = 0;
                        tracking.cyclesWithoutBuy = 0;
                        
                        console.log(`💰 Auto-pricing: ${item} ${oldPrice} → ${newPrice} (${reason})`);
                        
                        // Notify web clients
                        broadcastToWeb({ 
                            type: 'price_adjusted', 
                            item, 
                            oldPrice, 
                            newPrice, 
                            reason 
                        });
                        
                        res.json({ 
                            success: true, 
                            tracking, 
                            adjusted: true, 
                            oldPrice, 
                            newPrice, 
                            reason 
                        });
                    });
                } catch (e) {
                    res.json({ success: true, tracking, adjusted: false });
                }
            });
        } catch (e) {
            res.json({ success: true, tracking, adjusted: false });
        }
    });
});

// API: Get buy cycle tracking
app.get('/api/buy_cycle_tracking', (req, res) => {
    res.json({ tracking: buyCycleTracking });
});

// API: Reset buy cycle tracking
app.post('/api/buy_cycle_tracking/reset', (req, res) => {
    const { item } = req.body;
    if (item) {
        const normalizedName = item.toLowerCase().trim();
        delete buyCycleTracking[normalizedName];
        console.log(`🔄 Reset buy cycle tracking for: ${item}`);
    } else {
        buyCycleTracking = {};
        console.log('🔄 Reset all buy cycle tracking');
    }
    res.json({ success: true, tracking: buyCycleTracking });
});

// API: Get sell config
app.get('/api/sell_config', (req, res) => {
    const fs = require('fs');
    const file = path.join(__dirname, 'public', 'sell_config.json');
    fs.readFile(file, 'utf8', (err, data) => {
        if (err) return res.status(500).json({ error: err.message });
        try {
            const config = JSON.parse(data);
            // Include soldCounts so client knows remaining stock
            config.soldCounts = soldCounts;
            res.json(config);
        } catch (e) {
            res.status(500).json({ error: e.message });
        }
    });
});

// API: Save sell config
app.post('/api/sell_config', (req, res) => {
    const fs = require('fs');
    const file = path.join(__dirname, 'public', 'sell_config.json');
    fs.writeFile(file, JSON.stringify(req.body, null, 2), err => {
        if (err) return res.status(500).json({ success: false, error: err.message });
        res.json({ success: true });
    });
});

// API: Add new item to sell config
app.post('/api/sell_config/add', (req, res) => {
    const fs = require('fs');
    const file = path.join(__dirname, 'public', 'sell_config.json');
    const { name, position, quantityPerListing } = req.body;
    if (!name) return res.status(400).json({ success: false, error: 'Name required' });
    
    fs.readFile(file, 'utf8', (err, data) => {
        if (err) return res.status(500).json({ success: false, error: err.message });
        try {
            const config = JSON.parse(data);
            if (!config.items[name]) {
                config.items[name] = { 
                    enabled: false, 
                    listingsCount: 1,
                    quantityPerListing: quantityPerListing || 1, 
                    minPrice: 0, 
                    maxPrice: 0,
                    position: position || [0, 0],
                    stock: 10  // Default stock
                };
            }
            fs.writeFile(file, JSON.stringify(config, null, 2), err => {
                if (err) return res.status(500).json({ success: false, error: err.message });
                res.json({ success: true, config });
            });
        } catch (e) {
            res.status(500).json({ success: false, error: e.message });
        }
    });
});

// API: Reset sold counts (for restocking)
app.post('/api/sold_counts/reset', (req, res) => {
    const { item } = req.body;
    if (item) {
        // Reset specific item
        const normalizedName = item.toLowerCase().trim();
        delete soldCounts[normalizedName];
        console.log(`📦 Reset sold count for: ${item}`);
    } else {
        // Reset all
        soldCounts = {};
        console.log('📦 Reset all sold counts');
    }
    res.json({ success: true, soldCounts });
});

// API: Get sold counts
app.get('/api/sold_counts', (req, res) => {
    res.json({ soldCounts });
});

// API: Update stock for a specific item
app.post('/api/sell_config/stock', (req, res) => {
    const fs = require('fs');
    const file = path.join(__dirname, 'public', 'sell_config.json');
    const { item, stock } = req.body;
    
    if (!item || stock === undefined) {
        return res.status(400).json({ success: false, error: 'Item name and stock required' });
    }
    
    fs.readFile(file, 'utf8', (err, data) => {
        if (err) return res.status(500).json({ success: false, error: err.message });
        try {
            const config = JSON.parse(data);
            
            // Find matching item (case-insensitive)
            let matchedKey = null;
            for (const key of Object.keys(config.items || {})) {
                if (key.toLowerCase().trim() === item.toLowerCase().trim()) {
                    matchedKey = key;
                    break;
                }
            }
            
            if (!matchedKey) {
                return res.status(404).json({ success: false, error: `Item not found: ${item}` });
            }
            
            const oldStock = config.items[matchedKey].stock || 0;
            config.items[matchedKey].stock = parseInt(stock) || 0;
            
            console.log(`📦 Stock updated: ${matchedKey} - ${oldStock} -> ${stock}`);
            
            fs.writeFile(file, JSON.stringify(config, null, 2), err => {
                if (err) return res.status(500).json({ success: false, error: err.message });
                
                // Broadcast update to web clients
                broadcastToWeb({ 
                    type: 'stock_updated', 
                    item: matchedKey, 
                    oldStock, 
                    newStock: parseInt(stock) || 0 
                });
                
                res.json({ success: true, item: matchedKey, oldStock, newStock: parseInt(stock) || 0 });
            });
        } catch (e) {
            res.status(500).json({ success: false, error: e.message });
        }
    });
});

// API: Bulk update stock for multiple items
app.post('/api/sell_config/stock/bulk', (req, res) => {
    const fs = require('fs');
    const file = path.join(__dirname, 'public', 'sell_config.json');
    const { stocks } = req.body;  // { "Item Name": quantity, ... }
    
    if (!stocks || typeof stocks !== 'object') {
        return res.status(400).json({ success: false, error: 'stocks object required' });
    }
    
    fs.readFile(file, 'utf8', (err, data) => {
        if (err) return res.status(500).json({ success: false, error: err.message });
        try {
            const config = JSON.parse(data);
            const results = [];
            
            for (const [itemName, stockValue] of Object.entries(stocks)) {
                // Find matching item (case-insensitive)
                let matchedKey = null;
                for (const key of Object.keys(config.items || {})) {
                    if (key.toLowerCase().trim() === itemName.toLowerCase().trim()) {
                        matchedKey = key;
                        break;
                    }
                }
                
                if (matchedKey) {
                    const oldStock = config.items[matchedKey].stock || 0;
                    config.items[matchedKey].stock = parseInt(stockValue) || 0;
                    results.push({ item: matchedKey, oldStock, newStock: parseInt(stockValue) || 0 });
                    console.log(`📦 Stock updated: ${matchedKey} - ${oldStock} -> ${stockValue}`);
                } else {
                    results.push({ item: itemName, error: 'not found' });
                }
            }
            
            fs.writeFile(file, JSON.stringify(config, null, 2), err => {
                if (err) return res.status(500).json({ success: false, error: err.message });
                
                // Broadcast update
                broadcastToWeb({ type: 'stocks_updated', results });
                
                res.json({ success: true, results });
            });
        } catch (e) {
            res.status(500).json({ success: false, error: e.message });
        }
    });
});

// Legacy: Save buy thresholds (redirect to config)
app.post('/api/buy_thresholds', (req, res) => {
    res.status(400).json({ success: false, error: 'Use /api/buy_config instead' });
});

// API: Get session stats
app.get('/api/session_stats', (req, res) => {
    res.json(getSessionStatsSummary());
});

// API: Reset session stats
app.post('/api/session_stats/reset', (req, res) => {
    sessionStats = {
        startTime: Date.now(),
        bought: [],
        sold: [],
        lowestPrices: {},
        marketPrices: {},
        currentListingsCount: 0,
        currentListingsValue: 0
    };
    // Clear previous listings so we don't detect false "sold" items
    previousListings = null;
    // Reset sold counts for stock tracking
    soldCounts = {};
    // Reset buy counts for max per session limit
    boughtCounts = {};
    buyUnderAvgSessionCount = 0;
    console.log('📦 Session reset - sold/buy counts cleared');
    broadcastToWeb({ type: 'session_reset', stats: getSessionStatsSummary() });
    res.json({ success: true, message: 'Session stats reset' });
});

server.listen(PORT, () => {
    console.log(`Hero Siege Market Calculator running at http://localhost:${PORT}`);
    console.log(`Control panel at http://localhost:${PORT}/control`);
    console.log(`WebSocket server running at ws://localhost:${PORT}/ws`);
});
