CREATE TABLE customers (id INTEGER PRIMARY KEY, name TEXT NOT NULL, email TEXT NOT NULL);
INSERT INTO customers VALUES (1,'Edsger Dijkstra','edsger@example.com'),(2,'Barbara Liskov','barbara@example.com'),(3,'Donald Knuth','donald@example.com');
CREATE TABLE orders (id INTEGER PRIMARY KEY, customer_id INTEGER NOT NULL REFERENCES customers(id), total_cents INTEGER NOT NULL);
INSERT INTO orders VALUES (1,1,900),(2,1,800),(3,1,700),(4,2,1500),(5,3,1100),(6,3,1000);
