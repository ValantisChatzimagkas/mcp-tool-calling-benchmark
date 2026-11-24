CREATE TABLE invoices (id INTEGER PRIMARY KEY, customer TEXT NOT NULL, amount_cents INTEGER NOT NULL, paid INTEGER NOT NULL);
INSERT INTO invoices VALUES (1,'Ada Lovelace',4200,0),(2,'Grace Hopper',1800,1),(3,'Alan Turing',2500,0),(4,'Ada Lovelace',900,0);
